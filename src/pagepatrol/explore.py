import asyncio
import contextlib
import posixpath
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urldefrag, urlparse

from playwright.async_api import Browser, Page, async_playwright
from playwright.async_api import Error as PlaywrightError

from pagepatrol import __version__
from pagepatrol.agent import replayable_spec, take_snapshot
from pagepatrol.browsers import DEFAULT_BROWSER, context_options, launch
from pagepatrol.check import NAVIGATION_TIMEOUT_MS, RuntimeCollector
from pagepatrol.checks import load_js
from pagepatrol.checks.perf import PerfProbe
from pagepatrol.models import RunReport, ScenarioResult, Severity, StepResult, Viewport
from pagepatrol.runner import (
    ConfirmFn,
    RunOptions,
    StepFailed,
    _PageChecks,
    _perform,
    _settle,
    _slug,
    is_local,
    looks_destructive,
)
from pagepatrol.sample import option_for, value_for
from pagepatrol.scenario import Step

MAX_CLICKS_PER_PAGE = 8
MAX_FORMS_PER_PAGE = 3
CLICK_ROLES = {"button", "tab", "switch", "menuitem"}

LOGOUT = re.compile(r"\b(log ?out|sign ?out|çıkış( yap)?|oturumu kapat)\b", re.IGNORECASE)
NOT_PAGES = re.compile(
    r"\.(pdf|zip|gz|tgz|rar|7z|png|jpe?g|gif|svg|webp|ico|mp4|webm|mp3|wav|"
    r"docx?|xlsx?|pptx?|csv|exe|dmg|msi|apk|json|xml|txt)$",
    re.IGNORECASE,
)


@dataclass
class ExploreOptions:
    out_dir: Path
    viewport: Viewport
    max_pages: int = 15
    # Click buttons and submit forms. None means only on localhost and local files.
    interact: bool | None = None
    confirm: ConfirmFn | None = None
    storage_state: Path | None = None
    headed: bool = False
    browsers: list[str] = dataclass_field(default_factory=lambda: [DEFAULT_BROWSER])
    performance: bool = True
    throttle: bool = True


def same_site(url: str, start: str) -> bool:
    a, b = urlparse(url), urlparse(start)
    if b.scheme == "file":
        return a.scheme == "file" and a.path.startswith(posixpath.dirname(b.path) + "/")
    return a.scheme in ("http", "https") and a.netloc == b.netloc


def _page_key(url: str) -> str:
    return urldefrag(url).url.rstrip("/")


def _form_key(form: dict) -> tuple:
    return (form["action"], tuple(f["name"] for f in form["fields"]))


class _Explorer:
    def __init__(self, page: Page, start: str, options: ExploreOptions, browser: str):
        self.page = page
        self.browser = browser
        self.start = start
        self.options = options
        self.interact = is_local(start) if options.interact is None else options.interact
        self.run_options = RunOptions(out_dir=options.out_dir, confirm=options.confirm)
        self.runtime = RuntimeCollector()
        self.runtime.attach(page)
        self.checks = _PageChecks()
        self.probe = None
        if options.performance:
            phone = options.throttle and options.viewport.width < 1024
            self.probe = PerfProbe(browser, phone, is_local(start))
        self.queue: deque[str] = deque([start])
        self.seen = {_page_key(start)}
        # Site-wide, so a menu button that's on every page is tried once.
        self.tried: set[tuple[str, str]] = set()
        self.forms_tried: set[tuple] = set()

    def _display_path(self, url: str) -> str:
        path = urlparse(url).path or "/"
        if urlparse(self.start).scheme == "file":
            # Local files: show the path from the start folder, not from the drive root.
            root = posixpath.dirname(urlparse(self.start).path)
            return posixpath.relpath(path, root)
        return path

    def _enqueue(self, url: str) -> None:
        url = urldefrag(url).url
        key = _page_key(url)
        if key in self.seen or not same_site(url, self.start) or NOT_PAGES.search(url):
            return
        self.seen.add(key)
        self.queue.append(url)

    async def _step(
        self,
        result: ScenarioResult,
        description: str,
        action: str,
        run: Callable[[], Awaitable[dict | list[str] | None]],
        shots: Path,
    ) -> StepResult:
        step = StepResult(
            index=len(result.steps), action=action, description=description, status="passed"
        )
        result.steps.append(step)
        before = len(self.runtime.findings)
        document = await self.probe.document(self.page) if self.probe else None
        if self.probe and action == "goto":
            await self.probe.slow_down()
        t0 = time.perf_counter()
        try:
            outcome = await run()
            if isinstance(outcome, dict):
                step.resolved = outcome
            elif isinstance(outcome, list):
                step.actions = outcome
            await _settle(self.page)
        except StepFailed as exc:
            step.status = "failed"
            step.error = str(exc)
        step.duration_ms = int((time.perf_counter() - t0) * 1000)
        step.url = self.page.url
        perf_findings = []
        if self.probe:
            if step.status == "passed" and await self.probe.document(self.page) != document:
                step.performance, perf_findings = await self.probe.collect(self.page)
            await self.probe.speed_up()

        shot = shots / f"{step.index:02d}.png"
        with contextlib.suppress(PlaywrightError):
            await self.page.screenshot(path=shot)
            step.screenshot = shot.relative_to(self.options.out_dir).as_posix()
        step.findings = (
            await self.checks.run(self.page) + self.runtime.findings[before:] + perf_findings
        )
        step.findings.sort(key=lambda f: -f.severity.rank)
        return step

    async def _reopen(self, url: str) -> bool:
        try:
            await self.page.goto(url, timeout=NAVIGATION_TIMEOUT_MS)
            await _settle(self.page)
            return True
        except PlaywrightError:
            return False

    def _note_navigation(self, url: str) -> None:
        if _page_key(self.page.url) != _page_key(url):
            self._enqueue(self.page.url)

    async def explore_page(self, url: str, number: int) -> ScenarioResult:
        path = self._display_path(url)
        result = ScenarioResult(
            name=path,
            source=url,
            viewport=self.options.viewport,
            browser=self.browser,
            status="passed",
        )
        folder = f"{number:02d}-{_slug(path)}"
        if self.options.browsers != [DEFAULT_BROWSER]:
            folder = f"{self.browser}-{folder}"
        shots = self.options.out_dir / "screenshots" / folder
        shots.mkdir(parents=True, exist_ok=True)
        console_start = len(self.runtime.console)

        visit = await self._step(
            result,
            f"Open {path}",
            "goto",
            lambda: _perform(self.page, Step("goto", value=url), self.run_options),
            shots,
        )
        if visit.status == "failed":
            result.status = "failed"
            return result
        with contextlib.suppress(PlaywrightError):
            result.name = f"{await self.page.title() or path} ({path})"

        for href in await self.page.eval_on_selector_all("a[href]", "els => els.map(a => a.href)"):
            if not LOGOUT.search(href):
                self._enqueue(href)

        if self.interact:
            snapshot = await take_snapshot(self.page)
            forms = (await self.page.evaluate(load_js("js/forms.js")))["forms"]
            for form in forms[:MAX_FORMS_PER_PAGE]:
                await self._try_form(url, _form_key(form), result, shots)
            submits = {f["submit"] for f in forms if f["submit"]}
            for element in self._click_candidates(snapshot, submits)[:MAX_CLICKS_PER_PAGE]:
                await self._try_click(url, element, result, shots)

        result.console = self.runtime.console[console_start:]
        if any(f.severity == Severity.ERROR for s in result.steps for f in s.findings):
            result.status = "failed"
        return result

    def _click_candidates(self, snapshot, submits: set[int]) -> list[dict]:
        candidates = []
        for element in snapshot.elements:
            key = (element["role"], element["name"])
            name = element["name"]
            if (
                element["role"] not in CLICK_ROLES
                or element["id"] in submits
                or element.get("disabled")
                or not name
                or key in self.tried
                or looks_destructive(name)
                or LOGOUT.search(name)
            ):
                continue
            self.tried.add(key)
            candidates.append(element)
        return candidates

    async def _try_click(self, url: str, element: dict, result: ScenarioResult, shots: Path):
        # Each try starts from a fresh copy of the page, so one click
        # doesn't change what the next one does.
        if not await self._reopen(url):
            return
        snapshot = await take_snapshot(self.page)
        key = (element["role"], element["name"])
        match = next((e for e in snapshot.elements if (e["role"], e["name"]) == key), None)
        if match is None:
            return
        step = Step("click", target=await replayable_spec(self.page, match))
        await self._step(
            result,
            f"Click {element['name']!r}",
            "click",
            lambda: _perform(self.page, step, self.run_options),
            shots,
        )
        self._note_navigation(url)

    async def _try_form(self, url: str, key: tuple, result: ScenarioResult, shots: Path):
        if key in self.forms_tried:
            return
        self.forms_tried.add(key)
        if not await self._reopen(url):
            return
        snapshot = await take_snapshot(self.page)
        forms = (await self.page.evaluate(load_js("js/forms.js")))["forms"]
        form = next((f for f in forms if _form_key(f) == key), None)
        if (
            form is None
            or looks_destructive(form["submitName"])
            or LOGOUT.search(form["submitName"])
        ):
            return
        steps = await self._form_steps(form, snapshot)
        if not steps:
            return

        async def submit() -> list[str]:
            for s in steps:
                await _perform(self.page, s, self.run_options)
            return [s.describe() for s in steps]

        name = form["name"] or form["submitName"] or "form"
        await self._step(result, f"Fill in and send {name!r}", "form", submit, shots)
        self._note_navigation(url)

    async def _form_steps(self, form: dict, snapshot) -> list[Step]:
        steps: list[Step] = []
        radios_done: set[str] = set()
        for field in form["fields"]:
            element = snapshot.element(field["id"])
            if element is None:
                continue
            spec = await replayable_spec(self.page, element)
            if field["type"] == "checkbox":
                if field["required"]:
                    steps.append(Step("check", target=spec))
            elif field["type"] == "radio":
                if field["name"] not in radios_done:
                    radios_done.add(field["name"])
                    steps.append(Step("check", target=spec))
            elif field["tag"] == "select":
                option = option_for(field)
                if option:
                    steps.append(Step("select", target=spec, value=option))
            elif (value := value_for(field)) is not None:
                steps.append(Step("fill", target=spec, value=value))

        submit = snapshot.element(form["submit"]) if form["submit"] else None
        if submit:
            steps.append(Step("click", target=await replayable_spec(self.page, submit)))
        elif steps:
            last = steps[-1]
            steps.append(Step("press", target=last.target, value="Enter"))
        return steps


async def _explore(start: str, options: ExploreOptions) -> RunReport:
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    results = []
    async with async_playwright() as p:
        for browser_name in options.browsers:
            browser: Browser = await launch(p, browser_name, headless=not options.headed)
            try:
                context = await browser.new_context(
                    **context_options(browser_name, options.viewport),
                    storage_state=str(options.storage_state) if options.storage_state else None,
                )
                page = await context.new_page()
                explorer = _Explorer(page, start, options, browser_name)
                if explorer.probe:
                    await explorer.probe.attach(page, slow_down=False)
                pages = 0
                while explorer.queue and pages < options.max_pages:
                    url = explorer.queue.popleft()
                    pages += 1
                    results.append(await explorer.explore_page(url, pages))
            finally:
                await browser.close()

    return RunReport(
        base_url=start,
        pagepatrol_version=__version__,
        started_at=started,
        duration_s=round(time.perf_counter() - t0, 2),
        scenarios=results,
    )


def explore(start: str, options: ExploreOptions) -> RunReport:
    return asyncio.run(_explore(start, options))
