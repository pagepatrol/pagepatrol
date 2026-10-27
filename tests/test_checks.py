from pathlib import Path

import pytest

from pagepatrol.browsers import BROWSERS, parse_browsers
from pagepatrol.check import CheckOptions, run_check
from pagepatrol.models import CheckReport, Severity
from pagepatrol.report import write_report
from pagepatrol.targets import VIEWPORTS

FIXTURES = Path(__file__).parent / "fixtures"


def check(
    name: str, tmp_path: Path, viewport: str = "desktop", browser: str = "chromium"
) -> CheckReport:
    options = CheckOptions(viewports=[VIEWPORTS[viewport]], out_dir=tmp_path, browsers=[browser])
    return run_check(str(FIXTURES / name), options)


def checks_found(report: CheckReport, min_severity: Severity = Severity.INFO) -> set[str]:
    return {f.check for f in report.all_findings() if f.severity.rank >= min_severity.rank}


@pytest.mark.parametrize("browser", BROWSERS)
@pytest.mark.parametrize("viewport", ["mobile", "desktop"])
def test_clean_page_has_no_findings(tmp_path, viewport, browser):
    report = check("clean.html", tmp_path, viewport, browser)
    assert report.all_findings() == []


@pytest.mark.parametrize(
    ("fixture", "expected", "severity", "viewport"),
    [
        ("overlap.html", "overlap", Severity.ERROR, "desktop"),
        ("low_contrast.html", "axe/color-contrast", Severity.WARNING, "desktop"),
        ("overflow.html", "horizontal-overflow", Severity.ERROR, "mobile"),
        ("clipped_text.html", "clipped-text", Severity.WARNING, "desktop"),
        ("small_text.html", "small-text", Severity.WARNING, "desktop"),
        ("broken_image.html", "broken-image", Severity.ERROR, "desktop"),
        ("broken_link.html", "broken-link", Severity.ERROR, "desktop"),
        ("js_error.html", "js-exception", Severity.ERROR, "desktop"),
        ("no_viewport.html", "viewport-meta", Severity.ERROR, "mobile"),
    ],
)
@pytest.mark.parametrize("browser", BROWSERS)
def test_detects_problem(tmp_path, fixture, expected, severity, viewport, browser):
    report = check(fixture, tmp_path, viewport, browser)
    matching = [f for f in report.all_findings() if f.check == expected]
    assert matching, f"expected {expected}, got {checks_found(report)}"
    assert matching[0].severity == severity


def test_overflow_only_on_narrow_screens(tmp_path):
    report = check("overflow.html", tmp_path, "desktop")
    assert "horizontal-overflow" not in checks_found(report)


@pytest.mark.parametrize("browser", BROWSERS)
def test_broken_image_is_reported_once(tmp_path, browser):
    report = check("broken_image.html", tmp_path, browser=browser)
    assert checks_found(report) == {"broken-image"}


def test_several_browsers_in_one_report(tmp_path):
    options = CheckOptions(
        viewports=[VIEWPORTS["mobile"]], out_dir=tmp_path, browsers=["firefox", "webkit"]
    )
    report = run_check(str(FIXTURES / "overflow.html"), options)
    assert [report.label(r) for r in report.results] == ["firefox mobile", "webkit mobile"]
    assert {r.screenshot for r in report.results} == {
        "screenshots/firefox-mobile.png",
        "screenshots/webkit-mobile.png",
    }
    html = write_report(report, tmp_path).read_text(encoding="utf-8")
    assert "webkit mobile" in html


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], ["chromium"]),
        (["safari"], ["webkit"]),
        (["firefox,chrome"], ["firefox", "chromium"]),
        (["all"], ["chromium", "firefox", "webkit"]),
        (["webkit", "all"], ["webkit", "chromium", "firefox"]),
    ],
)
def test_browser_names(values, expected):
    assert parse_browsers(values) == expected


def test_unknown_browser():
    with pytest.raises(ValueError, match="Unknown browser 'opera'"):
        parse_browsers(["opera"])


def test_intentional_excerpt_is_only_a_note(tmp_path):
    report = check("excerpt.html", tmp_path)
    clipped = [f for f in report.all_findings() if f.check == "clipped-text"]
    assert [f.severity for f in clipped] == [Severity.INFO]


def test_offscreen_skip_links_do_not_overlap(tmp_path):
    report = check("skip_link.html", tmp_path)
    assert "overlap" not in checks_found(report)


def test_report_files(tmp_path):
    report = check("overlap.html", tmp_path)
    html = write_report(report, tmp_path)

    assert html.exists()
    assert (tmp_path / "report.json").exists()
    result = report.results[0]
    assert (tmp_path / result.screenshot).exists()
    assert (tmp_path / result.annotated_screenshot).exists()
    assert result.findings[0].marker == 1
    assert "Text overlaps other text" in html.read_text(encoding="utf-8")


def test_page_is_back_at_the_top_before_capture():
    import asyncio

    from playwright.async_api import async_playwright

    from pagepatrol.check import _scroll_through

    async def scroll_position():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 800, "height": 600})
            await page.goto((FIXTURES / "smooth_scroll.html").as_uri())
            await _scroll_through(page)
            position = await page.evaluate("[scrollX, scrollY]")
            await browser.close()
            return position

    assert asyncio.run(scroll_position()) == [0, 0]
