import asyncio
import contextlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from playwright.async_api import Browser, Page, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from pagepatrol import __version__
from pagepatrol.check import NAVIGATION_TIMEOUT_MS, BrowserNotInstalled, RuntimeCollector
from pagepatrol.checks import install_helpers
from pagepatrol.checks.axe import run_axe
from pagepatrol.checks.layout import run_layout_checks
from pagepatrol.locate import TargetNotFound, accessible_name, resolve
from pagepatrol.models import Finding, RunReport, ScenarioResult, StepResult
from pagepatrol.scenario import Scenario, Step
from pagepatrol.targets import parse_viewport

ACTION_TIMEOUT_MS = 10_000
EXPECT_TIMEOUT_MS = 5_000

# Clicking one of these on a real site can't be undone. On localhost and
# local files we assume it's a test environment and don't ask.
DESTRUCTIVE = re.compile(
    r"\b(delete|remove|destroy|erase|pay|purchase|buy|checkout|place order|order now|"
    r"publish|unsubscribe|transfer|deactivate|close account|"
    r"sil|kaldır|öde|ödeme yap|satın al|sipariş ver|siparişi tamamla|yayınla|"
    r"abonelikten çık|hesabı kapat)\b",
    re.IGNORECASE,
)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

ConfirmFn = Callable[[str, str], bool]


class StepFailed(Exception):
    pass


@dataclass
class RunOptions:
    out_dir: Path
    base_url: str | None = None
    storage_state: Path | None = None
    confirm: ConfirmFn | None = None
    headed: bool = False


def is_local(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "file" or (parsed.hostname or "") in LOCAL_HOSTS


def looks_destructive(name: str) -> bool:
    return bool(DESTRUCTIVE.search(name))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "scenario"


async def _settle(page: Page) -> None:
    with contextlib.suppress(PlaywrightTimeout):
        await page.wait_for_load_state("load", timeout=5_000)
    with contextlib.suppress(PlaywrightTimeout):
        await page.wait_for_load_state("networkidle", timeout=2_000)
    await page.wait_for_timeout(150)


async def _wait_for(page: Page, condition: dict, timeout: int) -> None:
    key, value = next(iter(condition.items()))
    if key == "text":
        await page.get_by_text(str(value)).first.wait_for(state="visible", timeout=timeout)
    elif key == "no_text":
        await page.get_by_text(str(value)).first.wait_for(state="hidden", timeout=timeout)
    elif key == "url":
        await page.wait_for_url(lambda u: str(value) in u, timeout=timeout)
    elif key == "title":
        await page.wait_for_function(
            "t => document.title.includes(t)", arg=str(value), timeout=timeout
        )
    elif key in ("visible", "hidden"):
        if key == "visible":
            deadline = time.monotonic() + timeout / 1000
            while True:
                try:
                    await resolve(page, value, "click")
                    return
                except TargetNotFound:
                    if time.monotonic() > deadline:
                        raise
                    await page.wait_for_timeout(200)
        try:
            locator, _ = await resolve(page, value, "click")
        except TargetNotFound:
            return
        await locator.wait_for(state="hidden", timeout=timeout)


def _describe_failure(step: Step, page: Page) -> str:
    key, value = next(iter(step.value.items()))
    what = {
        "text": f"text {value!r} to appear",
        "no_text": f"text {value!r} to be gone",
        "url": f"URL to contain {value!r}",
        "title": f"title to contain {value!r}",
        "visible": f"{value!r} to be visible",
        "hidden": f"{value!r} to be hidden",
    }[key]
    return f"Expected {what}, but it didn't happen. Page is at {page.url}"


async def _perform(page: Page, step: Step, options: RunOptions) -> dict | None:
    """Run one step. Returns how the target was resolved, if there was one."""
    resolved = None
    locator = None
    if step.target is not None:
        try:
            locator, resolved = await resolve(page, step.target, step.action)
        except TargetNotFound as exc:
            raise StepFailed(str(exc)) from exc

    if step.action == "click" and options.confirm and not is_local(page.url):
        name = await accessible_name(locator)
        if looks_destructive(name) and not options.confirm(name, page.url):
            raise StepFailed(
                f"Stopped before clicking {name!r} on {page.url}: it may not be undoable. "
                "Confirm when asked, or pass --yes."
            )

    try:
        match step.action:
            case "goto":
                base = options.base_url or page.url
                await page.goto(urljoin(base, step.value), timeout=NAVIGATION_TIMEOUT_MS)
            case "click":
                await locator.click(timeout=ACTION_TIMEOUT_MS)
            case "hover":
                await locator.hover(timeout=ACTION_TIMEOUT_MS)
            case "check":
                await locator.check(timeout=ACTION_TIMEOUT_MS)
            case "uncheck":
                await locator.uncheck(timeout=ACTION_TIMEOUT_MS)
            case "fill":
                await locator.fill(str(step.value), timeout=ACTION_TIMEOUT_MS)
            case "select":
                try:
                    await locator.select_option(label=str(step.value), timeout=ACTION_TIMEOUT_MS)
                except PlaywrightError:
                    await locator.select_option(str(step.value), timeout=ACTION_TIMEOUT_MS)
            case "press":
                if locator is not None:
                    await locator.press(str(step.value), timeout=ACTION_TIMEOUT_MS)
                else:
                    await page.keyboard.press(str(step.value))
            case "wait":
                if isinstance(step.value, (int, float)):
                    await page.wait_for_timeout(step.value)
                else:
                    await _wait_for(page, step.value, ACTION_TIMEOUT_MS)
            case "expect":
                try:
                    await _wait_for(page, step.value, EXPECT_TIMEOUT_MS)
                except (PlaywrightTimeout, TargetNotFound) as exc:
                    raise StepFailed(_describe_failure(step, page)) from exc
            case "do":
                raise StepFailed(
                    "Plain-language steps need the AI executor, which isn't available yet. "
                    "Write this step with click/fill/expect for now."
                )
    except PlaywrightTimeout as exc:
        raise StepFailed(f"Timed out: {str(exc).splitlines()[0]}") from exc
    except PlaywrightError as exc:
        raise StepFailed(str(exc).splitlines()[0]) from exc
    return resolved


class _PageChecks:
    """Runs the layout and accessibility checks after each step and keeps only
    what's new, so a problem shows up once, at the step that caused it."""

    def __init__(self):
        self.seen: set[tuple] = set()
        self.axe_urls: set[str] = set()

    async def run(self, page: Page) -> list[Finding]:
        findings: list[Finding] = []
        try:
            await install_helpers(page)
            findings += (await run_layout_checks(page)).findings
            url = urldefrag(page.url).url
            if url not in self.axe_urls:
                self.axe_urls.add(url)
                findings += await run_axe(page)
        except PlaywrightError:
            # The page navigated away mid-check; the next step will catch it.
            return []

        new = []
        for f in findings:
            key = (f.check, f.selector, f.message)
            if key not in self.seen:
                self.seen.add(key)
                new.append(f)
        return new


async def _run_scenario(
    browser: Browser, scenario: Scenario, options: RunOptions
) -> ScenarioResult:
    viewport = parse_viewport(scenario.viewport)
    result = ScenarioResult(
        name=scenario.name,
        source=str(scenario.source) if scenario.source else None,
        viewport=viewport,
        status="passed",
    )
    mobile = viewport.width < 1024
    context = await browser.new_context(
        viewport={"width": viewport.width, "height": viewport.height},
        is_mobile=mobile,
        has_touch=mobile,
        storage_state=str(options.storage_state) if options.storage_state else None,
    )
    shots = options.out_dir / "screenshots" / _slug(scenario.name)
    shots.mkdir(parents=True, exist_ok=True)
    runtime = RuntimeCollector()
    checks = _PageChecks()

    try:
        page = await context.new_page()
        runtime.attach(page)

        start_url = scenario.url or options.base_url
        steps = list(scenario.steps)
        if start_url:
            if options.base_url and scenario.url:
                start_url = urljoin(options.base_url, scenario.url)
            steps.insert(0, Step("goto", value=start_url, raw={"goto": start_url}))
        elif not steps or steps[0].action != "goto":
            raise StepFailed("No start URL. Give the scenario a `url` or pass --base-url.")

        failed = False
        for i, step in enumerate(steps):
            step_result = StepResult(
                index=i, action=step.action, description=step.describe(), status="passed"
            )
            result.steps.append(step_result)
            if failed:
                step_result.status = "skipped"
                continue

            before = len(runtime.findings)
            t0 = time.perf_counter()
            try:
                step_result.resolved = await _perform(page, step, options)
                await _settle(page)
            except StepFailed as exc:
                step_result.status = "failed"
                step_result.error = str(exc)
                failed = True
            step_result.duration_ms = int((time.perf_counter() - t0) * 1000)
            step_result.url = page.url

            shot = shots / f"{i:02d}.png"
            with contextlib.suppress(PlaywrightError):
                await page.screenshot(path=shot)
                step_result.screenshot = shot.relative_to(options.out_dir).as_posix()

            step_result.findings = await checks.run(page) + runtime.findings[before:]
            step_result.findings.sort(key=lambda f: -f.severity.rank)

        if failed:
            result.status = "failed"
    except StepFailed as exc:
        result.status = "failed"
        result.steps.append(
            StepResult(
                index=0, action="setup", description="Start", status="failed", error=str(exc)
            )
        )
    finally:
        result.console = runtime.console
        await context.close()
    return result


async def _run(scenarios: list[Scenario], options: RunOptions) -> RunReport:
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=not options.headed)
        except PlaywrightError as exc:
            if "Executable doesn't exist" in str(exc):
                raise BrowserNotInstalled(
                    "Chromium isn't installed yet. Run: pagepatrol install"
                ) from exc
            raise
        try:
            results = [await _run_scenario(browser, s, options) for s in scenarios]
        finally:
            await browser.close()

    return RunReport(
        base_url=options.base_url,
        pagepatrol_version=__version__,
        started_at=started,
        duration_s=round(time.perf_counter() - t0, 2),
        scenarios=results,
    )


def run_scenarios(scenarios: list[Scenario], options: RunOptions) -> RunReport:
    return asyncio.run(_run(scenarios, options))
