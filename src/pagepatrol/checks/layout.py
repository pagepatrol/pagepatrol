from dataclasses import dataclass, field

from playwright.async_api import Page

from pagepatrol.checks import load_js
from pagepatrol.models import Finding, Rect


@dataclass
class PageLink:
    href: str
    selector: str
    rect: Rect | None


@dataclass
class LayoutResult:
    findings: list[Finding]
    links: list[PageLink] = field(default_factory=list)
    # How many findings each check produced before the per-check cap.
    totals: dict[str, int] = field(default_factory=dict)


async def run_layout_checks(page: Page) -> LayoutResult:
    raw = await page.evaluate(load_js("js/layout.js"))
    links = [
        PageLink(
            href=link["href"], selector=link["selector"], rect=link["rect"] and Rect(**link["rect"])
        )
        for link in raw["links"]
    ]
    return LayoutResult(
        findings=[Finding(**f) for f in raw["findings"]],
        links=links,
        totals=raw["totals"],
    )
