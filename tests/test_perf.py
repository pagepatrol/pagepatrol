from pathlib import Path

import pytest

from pagepatrol.browsers import BROWSERS
from pagepatrol.check import CheckOptions, run_check
from pagepatrol.checks.perf import PHONE_LABEL, findings_for, rating
from pagepatrol.cli import _solid_png
from pagepatrol.models import Performance, Severity
from pagepatrol.targets import VIEWPORTS

FIXTURES = Path(__file__).parent / "fixtures"


def check(target: Path, tmp_path: Path, browser="chromium", viewport="desktop", **options):
    options = CheckOptions(
        viewports=[VIEWPORTS[viewport]], out_dir=tmp_path / "out", browsers=[browser], **options
    )
    return run_check(str(target), options)


def perf_findings(report) -> dict:
    return {f.check: f for f in report.all_findings() if f.category == "performance"}


@pytest.mark.parametrize("browser", BROWSERS)
def test_clean_page_is_fast(tmp_path, browser):
    report = check(FIXTURES / "clean.html", tmp_path, browser)
    perf = report.results[0].performance
    assert perf is not None and perf.fcp_ms is not None and perf.requests >= 1
    assert perf_findings(report) == {}


def test_layout_shift_points_at_what_moved(tmp_path):
    report = check(FIXTURES / "layout_shift.html", tmp_path)
    shift = perf_findings(report)["perf/cls"]
    assert shift.severity == Severity.WARNING
    assert report.results[0].performance.cls > 0.25
    assert shift.selector and shift.rect is not None


def test_busy_main_thread(tmp_path):
    report = check(FIXTURES / "busy.html", tmp_path)
    blocked = perf_findings(report)["perf/tbt"]
    assert blocked.severity == Severity.WARNING
    assert report.results[0].performance.tbt_ms > 600


@pytest.mark.parametrize("browser", ["firefox", "webkit"])
def test_unsupported_metrics_are_left_out(tmp_path, browser):
    report = check(FIXTURES / "layout_shift.html", tmp_path, browser)
    perf = report.results[0].performance
    assert perf.cls is None and perf.tbt_ms is None
    assert "perf/cls" not in perf_findings(report)


@pytest.mark.parametrize("browser", BROWSERS)
def test_oversized_image(tmp_path, browser):
    (tmp_path / "photo.png").write_bytes(_solid_png(1600, 1200, (40, 90, 160)))
    page = tmp_path / "index.html"
    page.write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Photo</title>'
        '</head><body><img src="photo.png" alt="Photo" width="200" height="150"></body></html>',
        encoding="utf-8",
    )
    image = perf_findings(check(page, tmp_path, browser))["perf/oversized-image"]
    assert "1600×1200" in image.message and "200×150" in image.message
    assert image.rect is not None


def test_phone_throttling_is_chromium_only(tmp_path):
    chromium = check(FIXTURES / "clean.html", tmp_path, "chromium", "mobile")
    assert chromium.results[0].performance.throttling == PHONE_LABEL
    desktop = check(FIXTURES / "clean.html", tmp_path, "chromium", "desktop")
    assert desktop.results[0].performance.throttling is None
    firefox = check(FIXTURES / "clean.html", tmp_path, "firefox", "mobile")
    assert firefox.results[0].performance.throttling is None
    off = check(FIXTURES / "clean.html", tmp_path, "chromium", "mobile", throttle=False)
    assert off.results[0].performance.throttling is None


def test_performance_can_be_turned_off(tmp_path):
    report = check(FIXTURES / "busy.html", tmp_path, performance=False)
    assert report.results[0].performance is None
    assert perf_findings(report) == {}


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("lcp_ms", 2400, "good"),
        ("lcp_ms", 3000, "needs-improvement"),
        ("lcp_ms", 4100, "poor"),
        ("cls", 0.1, "good"),
        ("cls", 0.3, "poor"),
        ("tbt_ms", None, None),
    ],
)
def test_rating(key, value, expected):
    assert rating(key, value) == expected


def test_slow_metrics_become_findings():
    perf = Performance(lcp_ms=5200, fcp_ms=2000, ttfb_ms=100, requests=3, transfer_bytes=1000)
    found = {f.check: f for f in findings_for(perf, {}, local=False)}
    assert found["perf/lcp"].severity == Severity.WARNING
    assert "5.2s" in found["perf/lcp"].message
    assert found["perf/fcp"].severity == Severity.INFO
    assert "perf/ttfb" not in found


def test_heavy_pages_on_dev_servers_are_only_a_note():
    perf = Performance(fcp_ms=100, requests=400, transfer_bytes=8 * 1024 * 1024)
    raw = {"large": [{"url": "http://localhost:5173/app.js", "type": "script", "bytes": 3_000_000}]}
    local = {f.check: f for f in findings_for(perf, raw, local=True)}
    assert local["perf/page-weight"].severity == Severity.INFO
    assert "dev server" in local["perf/large-file"].message
    remote = {f.check: f for f in findings_for(perf, raw, local=False)}
    assert remote["perf/page-weight"].severity == Severity.WARNING
    assert remote["perf/large-file"].severity == Severity.WARNING
