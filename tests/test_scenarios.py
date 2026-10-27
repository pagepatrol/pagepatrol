from pathlib import Path

import pytest
import yaml

from pagepatrol.report import write_run_report
from pagepatrol.runner import RunOptions, is_local, looks_destructive, run_scenarios
from pagepatrol.scenario import ScenarioError, load_scenarios, parse_step

APP = Path(__file__).parent / "fixtures" / "app"
BASE = (APP / "index.html").as_uri()


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "scenario.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def run(tmp_path: Path, text: str, **options):
    scenarios = load_scenarios(write(tmp_path, text))
    return run_scenarios(scenarios, RunOptions(out_dir=tmp_path / "out", base_url=BASE, **options))


CONTACT = """
name: Contact form sends a message
steps:
  - click: Contact us
  - expect: { url: contact.html }
  - fill: { target: Email, value: test@example.com }
  - fill: { target: "How can we help?", value: Hello }
  - click: Send
  - expect: Thanks for your message
"""


def test_scenario_passes_and_checks_every_step(tmp_path):
    report = run(tmp_path, CONTACT)
    scenario = report.scenarios[0]

    assert scenario.status == "passed", [s.error for s in scenario.steps]
    assert [s.status for s in scenario.steps] == ["passed"] * 7  # goto + 6 steps
    assert all(s.screenshot for s in scenario.steps)

    # The thank-you toast lands on top of the heading. That must be reported
    # at the step that caused it, not earlier.
    send = next(s for s in scenario.steps if s.description == "Click Send")
    assert any(f.check == "overlap" for f in send.findings)
    before = [s for s in scenario.steps if s.index < send.index]
    assert not any(f.check == "overlap" for s in before for f in s.findings)

    html = write_run_report(report, tmp_path / "out").read_text(encoding="utf-8")
    assert "1 of 1 scenario passed" in html


def test_recorded_targets_replay(tmp_path):
    original = yaml.safe_load(CONTACT)["steps"]
    first = run(tmp_path, CONTACT).scenarios[0]

    # Swap every description for the exact spec it resolved to and run again.
    replay = []
    for raw, result in zip(original, first.steps[1:], strict=True):
        action, arg = next(iter(raw.items()))
        if result.resolved is None:
            replay.append(raw)
        elif action == "fill":
            replay.append({"fill": {**result.resolved, "value": arg["value"]}})
        else:
            replay.append({action: result.resolved})

    second = run(tmp_path, yaml.safe_dump({"steps": replay})).scenarios[0]
    assert second.status == "passed", [s.error for s in second.steps]
    assert [s.resolved for s in second.steps] == [s.resolved for s in first.steps]


def test_failed_expectation_stops_the_scenario(tmp_path):
    report = run(
        tmp_path,
        """
name: Wrong expectation
steps:
  - expect: Free shipping
  - click: Contact us
""",
    )
    scenario = report.scenarios[0]
    assert scenario.status == "failed"
    statuses = [s.status for s in scenario.steps]
    assert statuses == ["passed", "failed", "skipped"]
    assert "Free shipping" in scenario.steps[1].error


def test_missing_target_is_a_clear_failure(tmp_path):
    report = run(tmp_path, "steps:\n  - click: Checkout\n")
    step = report.scenarios[0].steps[1]
    assert step.status == "failed"
    assert "Couldn't find 'Checkout'" in step.error


def test_no_confirmation_needed_on_local_pages(tmp_path):
    asked = []
    report = run(
        tmp_path,
        "steps:\n  - click: Delete account\n  - expect: Account deleted\n",
        confirm=lambda name, url: asked.append(name) or False,
    )
    assert report.scenarios[0].status == "passed"
    assert asked == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Delete account", True),
        ("Pay now", True),
        ("Satın al", True),
        ("Siparişi tamamla", True),
        ("Hesabı sil", True),
        ("Send", False),
        ("Silver plan", False),
        ("Contact us", False),
    ],
)
def test_destructive_names(name, expected):
    assert looks_destructive(name) is expected


def test_local_urls():
    assert is_local("file:///C:/site/index.html")
    assert is_local("http://localhost:3000/")
    assert is_local("http://127.0.0.1:8000/x")
    assert not is_local("https://example.com/")


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"click": "A", "fill": "B"}, "exactly one action"),
        ({"jump": "A"}, "unknown action"),
        ({"fill": "Email"}, "needs a target and a value"),
        ({"expect": {"colour": "red"}}, "takes text, url"),
        ({"goto": 5}, "needs a URL"),
    ],
)
def test_step_validation(raw, message):
    with pytest.raises(ScenarioError, match=message):
        parse_step(raw, "test")


def test_several_scenarios_in_one_file(tmp_path):
    path = write(
        tmp_path,
        """
scenarios:
  - name: One
    steps: [{ click: Contact us }]
  - name: Two
    viewport: mobile
    steps: [{ expect: Demo shop }]
""",
    )
    scenarios = load_scenarios(path)
    assert [s.name for s in scenarios] == ["One", "Two"]
    assert scenarios[1].viewport == "mobile"


def test_empty_folder_is_an_error(tmp_path):
    with pytest.raises(ScenarioError, match="No scenario files"):
        load_scenarios(tmp_path)
