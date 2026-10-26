import asyncio
import contextlib
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from pagepatrol import __version__
from pagepatrol.checks import install_helpers, load_js
from pagepatrol.checks.axe import run_axe
from pagepatrol.checks.layout import run_layout_checks
from pagepatrol.checks.links import check_links
from pagepatrol.llm import Client
from pagepatrol.models import (
    CheckReport,
    ConsoleMessage,
    Finding,
    LLMRun,
    Severity,
    Viewport,
    ViewportResult,
)
from pagepatrol.review import DEPTHS, Depth, Tile, review_viewport
from pagepatrol.targets import resolve_target

NAVIGATION_TIMEOUT_MS = 30_000
NETWORK_IDLE_TIMEOUT_MS = 5_000
MAX_CONSOLE_MESSAGES = 200


class BrowserNotInstalled(RuntimeError):
    pass


class PageLoadError(RuntimeError):
    pass


@dataclass
class CheckOptions:
    viewports: list[Viewport]
    out_dir: Path
    check_links: bool = True
    external_links: bool = False
    llm: Client | None = None
    llm_provider: str | None = None
    depth: Depth = Depth.BALANCED


@dataclass
class _Runtime:
    console: list[ConsoleMessage] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    seen_urls: set[str] = field(default_factory=set)

    def attach(self, page: Page) -> None:
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("requestfailed", self._on_request_failed)
        page.on("response", self._on_response)

    def _on_console(self, msg) -> None:
        if len(self.console) < MAX_CONSOLE_MESSAGES:
            loc = msg.location
            where = f"{loc['url']}:{loc['lineNumber']}" if loc and loc.get("url") else None
            self.console.append(ConsoleMessage(type=msg.type, text=msg.text, location=where))
        # Resource failures also show up as console errors; the network
        # handlers below report those with better detail.
        if msg.type == "error" and not msg.text.startswith("Failed to load resource"):
            self.findings.append(
                Finding(
                    check="console-error",
                    category="runtime",
                    severity=Severity.ERROR,
                    message=msg.text[:500],
                )
            )

    def _on_page_error(self, error) -> None:
        self.findings.append(
            Finding(
                check="js-exception",
                category="runtime",
                severity=Severity.ERROR,
                message=f"Uncaught exception: {error}"[:500],
            )
        )

    def _on_request_failed(self, request) -> None:
        if request.url in self.seen_urls:
            return
        self.seen_urls.add(request.url)
        reason = request.failure or "unknown error"
        # Aborted requests are usually the browser cancelling something on
        # purpose (navigation, media preload), not a broken resource.
        severity = Severity.INFO if "ERR_ABORTED" in reason else Severity.ERROR
        self.findings.append(
            Finding(
                check="failed-request",
                category="network",
                severity=severity,
                message=f"Request failed: {reason}",
                details={"url": request.url, "resource_type": request.resource_type},
            )
        )

    def _on_response(self, response) -> None:
        if response.status < 400 or response.url in self.seen_urls:
            return
        self.seen_urls.add(response.url)
        is_favicon = response.url.rsplit("/", 1)[-1].startswith("favicon")
        self.findings.append(
            Finding(
                check="http-error",
                category="network",
                severity=Severity.INFO if is_favicon else Severity.ERROR,
                message=f"HTTP {response.status} for {response.request.resource_type}",
                details={"url": response.url, "status": response.status},
            )
        )


async def _scroll_through(page: Page) -> None:
    # Walk down the page so lazy-loaded images and scroll-triggered content
    # are there before we take screenshots and measure.
    await page.evaluate(
        """async () => {
          const step = Math.max(innerHeight * 0.8, 200);
          for (let i = 0; i < 40 && scrollY + innerHeight < document.documentElement.scrollHeight; i++) {
            scrollBy(0, step);
            await new Promise((r) => setTimeout(r, 120));
          }
          scrollTo(0, 0);
        }"""
    )
    await page.wait_for_timeout(300)


async def _open(context: BrowserContext, url: str, runtime: _Runtime) -> Page:
    page = await context.new_page()
    runtime.attach(page)
    try:
        response = await page.goto(url, wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
    except PlaywrightError as exc:
        raise PageLoadError(f"Couldn't open {url}: {str(exc).splitlines()[0]}") from exc
    if response is not None and response.status >= 400:
        raise PageLoadError(f"{url} returned HTTP {response.status}")
    # Pages with polling or analytics never go idle; that's fine.
    with contextlib.suppress(PlaywrightTimeout):
        await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
    await _scroll_through(page)
    return page


async def _capture_tiles(page: Page, viewport: Viewport, max_tiles: int) -> list[Tile]:
    # Models downscale very tall images until text is unreadable, so send the
    # page one screen at a time instead of as one long capture.
    height = await page.evaluate("document.documentElement.scrollHeight")
    tiles = []
    for i in range(min(math.ceil(height / viewport.height), max_tiles)):
        top = i * viewport.height
        tile_height = min(viewport.height, height - top)
        png = await page.screenshot(
            clip={"x": 0, "y": top, "width": viewport.width, "height": tile_height},
            full_page=True,
        )
        tiles.append(Tile(png=png, top=top, height=tile_height))
    return tiles


def _number_markers(findings: list[Finding]) -> list[dict]:
    markers = []
    for finding in findings:
        if finding.rect is None:
            continue
        finding.marker = len(markers) + 1
        markers.append(
            {"n": finding.marker, "severity": finding.severity.value, **finding.rect.model_dump()}
        )
    return markers


async def _check_viewport(
    browser: Browser,
    url: str,
    viewport: Viewport,
    options: CheckOptions,
    with_links: bool,
) -> ViewportResult:
    mobile = viewport.width < 1024
    context = await browser.new_context(
        viewport={"width": viewport.width, "height": viewport.height},
        is_mobile=mobile,
        has_touch=mobile,
        bypass_csp=True,
    )
    try:
        runtime = _Runtime()
        page = await _open(context, url, runtime)
        result = ViewportResult(viewport=viewport, page_title=await page.title())

        shots = options.out_dir / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        clean_shot = shots / f"{viewport.name}.png"
        await page.screenshot(path=clean_shot, full_page=True)
        result.screenshot = clean_shot.relative_to(options.out_dir).as_posix()
        tiles = []
        if options.llm:
            tiles = await _capture_tiles(page, viewport, DEPTHS[options.depth].max_tiles)

        await install_helpers(page)
        layout = await run_layout_checks(page)
        findings = layout.findings + await run_axe(page)

        if mobile and not await page.locator("meta[name=viewport]").count():
            findings.append(
                Finding(
                    check="viewport-meta",
                    category="responsive",
                    severity=Severity.ERROR,
                    message="No <meta name=viewport> tag, so phones render the page zoomed out",
                )
            )

        if with_links:
            findings += await check_links(
                layout.links, page.url, context.request, include_external=options.external_links
            )

        # A missing image is already reported as broken-image; don't repeat
        # the network error for the same file.
        broken_images = {f.details.get("src") for f in findings if f.check == "broken-image"}
        findings += [f for f in runtime.findings if f.details.get("url") not in broken_images]

        if options.llm and tiles:
            new_findings, review = await review_viewport(
                options.llm, viewport, tiles, findings, options.depth
            )
            findings += new_findings
            result.review_summary = review.summary
            result.review_error = review.error

        findings.sort(key=lambda f: -f.severity.rank)

        markers = _number_markers(findings)
        if markers:
            await page.evaluate(load_js("js/annotate.js"), markers)
            annotated = shots / f"{viewport.name}-annotated.png"
            await page.screenshot(path=annotated, full_page=True)
            result.annotated_screenshot = annotated.relative_to(options.out_dir).as_posix()

        result.findings = findings
        result.console = runtime.console
        return result
    finally:
        await context.close()


async def _run(target: str, url: str, options: CheckOptions) -> CheckReport:
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except PlaywrightError as exc:
            if "Executable doesn't exist" in str(exc):
                raise BrowserNotInstalled(
                    "Chromium isn't installed yet. Run: pagepatrol install"
                ) from exc
            raise
        try:
            results = []
            for i, viewport in enumerate(options.viewports):
                with_links = options.check_links and i == 0
                results.append(await _check_viewport(browser, url, viewport, options, with_links))
        finally:
            await browser.close()

    llm_run = None
    if options.llm:
        usage = options.llm.usage
        llm_run = LLMRun(
            provider=options.llm_provider or "unknown",
            model=options.llm.model,
            depth=options.depth.value,
            calls=usage.calls,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    return CheckReport(
        target=target,
        url=url,
        pagepatrol_version=__version__,
        started_at=started,
        duration_s=round(time.perf_counter() - t0, 2),
        results=results,
        llm=llm_run,
    )


def run_check(target: str, options: CheckOptions) -> CheckReport:
    url = resolve_target(target)
    return asyncio.run(_run(target, url, options))
