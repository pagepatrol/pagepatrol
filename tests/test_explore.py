from pathlib import Path

from pagepatrol.explore import ExploreOptions, explore, same_site
from pagepatrol.report import write_run_report
from pagepatrol.sample import option_for, value_for
from pagepatrol.targets import VIEWPORTS

SITE = Path(__file__).parent / "fixtures" / "site"
START = (SITE / "index.html").as_uri()


def run(tmp_path, **options):
    return explore(
        START, ExploreOptions(out_dir=tmp_path, viewport=VIEWPORTS["desktop"], **options)
    )


def test_follows_links_presses_buttons_and_sends_forms(tmp_path):
    report = run(tmp_path)
    pages = {s.source.rsplit("/", 1)[-1]: s for s in report.scenarios}

    # Same-site pages only; never the logout link or a download.
    assert set(pages) == {"index.html", "about.html", "missing.html"}
    assert pages["missing.html"].status == "failed"

    home = pages["index.html"]
    steps = {s.description: s for s in home.steps}
    assert "Click 'Show details'" in steps
    assert not any("Delete" in d for d in steps)

    more = steps["Click 'Load more'"]
    assert any(f.check == "js-exception" for f in more.findings), more.findings
    assert not any(f.check == "js-exception" for f in steps["Click 'Show details'"].findings)

    form = next(s for s in home.steps if s.action == "form")
    assert "Type 'test@example.com' into textbox 'Email'" in form.actions
    assert "Select 'News' in combobox 'Topic'" in form.actions
    assert any(f.check == "js-exception" for f in form.findings)

    html = write_run_report(report, tmp_path).read_text(encoding="utf-8")
    assert "Load more" in html


def test_without_interaction_only_links_are_followed(tmp_path):
    report = run(tmp_path, interact=False)
    assert all(s.action == "goto" for page in report.scenarios for s in page.steps)
    assert len(report.scenarios) == 3


def test_max_pages(tmp_path):
    assert len(run(tmp_path, max_pages=1, interact=False).scenarios) == 1


def test_same_site():
    assert same_site("http://localhost:3000/a", "http://localhost:3000/")
    assert not same_site("http://localhost:4000/a", "http://localhost:3000/")
    assert not same_site("https://example.com/", "http://localhost:3000/")
    assert same_site("file:///C:/site/sub/a.html", "file:///C:/site/index.html")
    assert not same_site("file:///C:/other/a.html", "file:///C:/site/index.html")


def test_sample_values():
    assert value_for({"type": "email", "tag": "input"}) == "test@example.com"
    assert value_for({"type": "text", "tag": "input", "name": "message"}).startswith("Hello")
    assert value_for({"type": "text", "tag": "input", "name": "phone"}) == "+15555550123"
    assert value_for({"type": "number", "tag": "input", "min": "5"}) == "5"
    assert value_for({"type": "text", "tag": "input", "name": "zip", "maxlength": 3}) == "100"
    assert value_for({"type": "checkbox", "tag": "input"}) is None
    options = [{"text": "Choose one", "value": ""}, {"text": "News", "value": "news"}]
    assert option_for({"options": options}) == "News"
