import contextlib
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import CDPSession, Page
from playwright.async_api import Error as PlaywrightError

from pagepatrol.checks import install_helpers, load_js
from pagepatrol.models import Finding, Performance, Rect, Severity

# Google's limits for "good" and "poor", as used by Lighthouse and PageSpeed Insights.
LIMITS: dict[str, tuple[str, float, float]] = {
    "lcp_ms": ("The main content", 2500, 4000),
    "fcp_ms": ("The first content", 1800, 3000),
    "ttfb_ms": ("The server", 800, 1800),
    "tbt_ms": ("The page", 200, 600),
    "cls": ("The layout", 0.1, 0.25),
}
CHECK_NAMES = {"lcp_ms": "lcp", "fcp_ms": "fcp", "ttfb_ms": "ttfb", "tbt_ms": "tbt", "cls": "cls"}

# Chrome DevTools' "Slow 4G" and a 4x slower processor: what Lighthouse uses for phones.
PHONE_NETWORK = {
    "offline": False,
    "latency": 562.5,
    "downloadThroughput": 1474.56 * 1024 / 8,
    "uploadThroughput": 675 * 1024 / 8,
}
PHONE_CPU = 4
PHONE_LABEL = "a mid-range phone on 4G (slower network, 4x slower processor)"

PAGE_WEIGHT_INFO = 2.5 * 1024 * 1024
PAGE_WEIGHT_WARNING = 5 * 1024 * 1024
LARGE_FILE_WARNING = 1024 * 1024
MAX_IMAGE_FINDINGS = 10


class PerfProbe:
    """Measures how fast a page loads. Attach before navigating, collect after load."""

    def __init__(self, browser: str, throttle: bool, local: bool):
        self.throttle = throttle and browser == "chromium"
        self.local = local
        self.session: CDPSession | None = None

    async def attach(self, page: Page) -> None:
        await page.add_init_script(load_js("js/perf_observe.js"))
        if self.throttle:
            self.session = await page.context.new_cdp_session(page)
            await self.session.send("Network.enable")
            await self.session.send("Network.emulateNetworkConditions", PHONE_NETWORK)
            await self.session.send("Emulation.setCPUThrottlingRate", {"rate": PHONE_CPU})

    async def collect(self, page: Page) -> tuple[Performance | None, list[Finding]]:
        try:
            await install_helpers(page)
            raw = await page.evaluate(load_js("js/perf.js"))
        except PlaywrightError:
            return None, []
        finally:
            await self._stop_throttling()
        m = raw["metrics"]
        perf = Performance(
            ttfb_ms=m["ttfb"],
            fcp_ms=m["fcp"],
            lcp_ms=m["lcp"],
            load_ms=m["load"],
            tbt_ms=m["tbt"],
            cls=m["cls"],
            requests=m["requests"],
            transfer_bytes=m["transfer"],
            throttling=PHONE_LABEL if self.throttle else None,
        )
        return perf, findings_for(perf, raw, self.local)

    async def _stop_throttling(self) -> None:
        # The rest of the checks don't need a slow phone, only the load did.
        if not self.session:
            return
        with contextlib.suppress(PlaywrightError):
            await self.session.send("Emulation.setCPUThrottlingRate", {"rate": 1})
            await self.session.send(
                "Network.emulateNetworkConditions",
                {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1},
            )
            await self.session.detach()
        self.session = None


def rating(key: str, value: float | None) -> str | None:
    if value is None:
        return None
    _, good, poor = LIMITS[key]
    return "good" if value <= good else "poor" if value > poor else "needs-improvement"


def format_metric(key: str, value: float) -> str:
    if key == "cls":
        return f"{value:.2f}"
    return f"{value / 1000:.1f}s" if value >= 1000 else f"{value:.0f}ms"


def format_bytes(count: int) -> str:
    if count >= 1024 * 1024:
        return f"{count / 1024 / 1024:.1f} MB"
    return f"{count / 1024:.0f} KB"


TILES = [
    ("lcp_ms", "Main content", "LCP"),
    ("fcp_ms", "First content", "FCP"),
    ("cls", "Layout shift", "CLS"),
    ("tbt_ms", "Blocked", "TBT"),
    ("ttfb_ms", "Server", "TTFB"),
]


def tiles(perf: Performance) -> list[dict[str, str | None]]:
    """The headline numbers, formatted and rated, for the report and the terminal."""
    out = []
    for key, label, short in TILES:
        value = getattr(perf, key)
        out.append(
            {
                "label": label,
                "short": short,
                "value": None if value is None else format_metric(key, value),
                "rating": rating(key, value),
            }
        )
    return out


def _message(key: str, value: float) -> str:
    subject, good, _ = LIMITS[key]
    shown, limit = format_metric(key, value), format_metric(key, good)
    return {
        "lcp_ms": f"{subject} takes {shown} to show up (good is under {limit})",
        "fcp_ms": f"{subject} takes {shown} to appear (good is under {limit})",
        "ttfb_ms": f"{subject} takes {shown} to start answering (good is under {limit})",
        "tbt_ms": f"{subject} is too busy to respond to taps for {shown} (good is under {limit})",
        "cls": f"{subject} jumps around while loading, shift score {shown} (good is under {limit})",
    }[key]


def _rect(where: dict | None) -> Rect | None:
    return Rect(**where["rect"]) if where and where.get("rect") else None


def _name(url: str) -> str:
    return urlparse(url).path.rsplit("/", 1)[-1] or url


def findings_for(perf: Performance, raw: dict[str, Any], local: bool) -> list[Finding]:
    findings = []
    where = {"lcp_ms": raw.get("lcpElement"), "cls": raw.get("clsElement")}
    for key, check in CHECK_NAMES.items():
        value = getattr(perf, key)
        level = rating(key, value)
        if level in (None, "good"):
            continue
        findings.append(
            Finding(
                check=f"perf/{check}",
                category="performance",
                severity=Severity.WARNING if level == "poor" else Severity.INFO,
                message=_message(key, value),
                selector=(where.get(key) or {}).get("selector"),
                rect=_rect(where.get(key)),
                details={"value": round(value, 3), "rating": level, "throttling": perf.throttling},
            )
        )

    # Development servers send unminified, unbundled code, so sizes there
    # say little about production. Still worth a note, not a warning.
    dev = " (on a dev server; production builds are usually smaller)" if local else ""
    if perf.transfer_bytes > PAGE_WEIGHT_INFO:
        big = perf.transfer_bytes > PAGE_WEIGHT_WARNING and not local
        findings.append(
            Finding(
                check="perf/page-weight",
                category="performance",
                severity=Severity.WARNING if big else Severity.INFO,
                message=f"The page downloads {format_bytes(perf.transfer_bytes)} "
                f"in {perf.requests} requests{dev}",
                details={"bytes": perf.transfer_bytes, "requests": perf.requests},
            )
        )

    oversized = set()
    for image in raw.get("images", []):
        (nw, nh), (sw, sh) = image["natural"], image["shown"]
        bytes_ = image["bytes"]
        # Twice the needed size in each direction, and big enough to matter.
        if nw * nh < 4 * max(sw * sh, 1) or nw * nh < 250_000:
            continue
        if bytes_ is not None and bytes_ < 100 * 1024:
            continue
        oversized.add(image["src"])
        if len(oversized) > MAX_IMAGE_FINDINGS:
            continue
        size = f", {format_bytes(bytes_)}" if bytes_ else ""
        findings.append(
            Finding(
                check="perf/oversized-image",
                category="performance",
                severity=Severity.WARNING
                if bytes_ and bytes_ > LARGE_FILE_WARNING
                else Severity.INFO,
                message=f"Image is {nw}×{nh}{size} but shown at {sw}×{sh}; "
                "a smaller version would load faster",
                selector=image["selector"],
                rect=Rect(**image["rect"]),
                details={"src": image["src"], "bytes": bytes_},
            )
        )

    for item in raw.get("large", []):
        if item["url"] in oversized:
            continue
        big = item["bytes"] > LARGE_FILE_WARNING and not local
        kind = {"img": "Image", "script": "Script", "link": "File", "css": "Stylesheet"}.get(
            item["type"], "File"
        )
        findings.append(
            Finding(
                check="perf/large-file",
                category="performance",
                severity=Severity.WARNING if big else Severity.INFO,
                message=f"{kind} {_name(item['url'])} is {format_bytes(item['bytes'])}{dev}",
                details={"url": item["url"], "bytes": item["bytes"]},
            )
        )

    blocking = raw.get("blocking", [])
    if blocking and rating("fcp_ms", perf.fcp_ms) not in (None, "good"):
        names = ", ".join(_name(b["url"]) for b in blocking[:5])
        more = f" and {len(blocking) - 5} more" if len(blocking) > 5 else ""
        findings.append(
            Finding(
                check="perf/render-blocking",
                category="performance",
                severity=Severity.INFO,
                message=f"{len(blocking)} file(s) hold up the first paint: {names}{more}",
                details={"files": [b["url"] for b in blocking]},
            )
        )
    return findings
