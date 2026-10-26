import asyncio
from pathlib import Path

import pytest

from pagepatrol.check import CheckOptions, run_check
from pagepatrol.cli import _solid_png, _vision_probe
from pagepatrol.config import LLMConfig
from pagepatrol.llm import LLMError, Request, Usage, parse_json
from pagepatrol.models import Finding, Rect, Severity
from pagepatrol.report import write_report
from pagepatrol.review import Tile, apply_review
from pagepatrol.targets import VIEWPORTS

FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
    def __init__(self, answer=None, error: str | None = None):
        self.model = "fake-model"
        self.usage = Usage()
        self.answer = answer or {"verdicts": [], "issues": [], "summary": "Looks fine."}
        self.error = error
        self.requests: list[Request] = []

    async def complete_json(self, request: Request):
        self.requests.append(request)
        self.usage.add(1000, 100)
        if self.error:
            raise LLMError(self.error)
        return self.answer

    async def list_models(self):
        return ["fake-model"]


def run(name: str, tmp_path: Path, client: FakeClient):
    options = CheckOptions(
        viewports=[VIEWPORTS["desktop"]],
        out_dir=tmp_path,
        llm=client,
        llm_config=LLMConfig("anthropic", "claude-sonnet-5-5"),
    )
    return run_check(str(FIXTURES / name), options)


def test_review_downgrades_intentional_findings_and_adds_new_ones(tmp_path):
    client = FakeClient(
        {
            "verdicts": [{"id": 0, "verdict": "intentional", "reason": "Stacked headline effect."}],
            "issues": [
                {
                    "tile": 1,
                    "severity": "warning",
                    "kind": "layout",
                    "description": "Heading is crammed against the edge",
                    "box": {"x": 10, "y": 20, "width": 200, "height": 30},
                }
            ],
            "summary": "One layout issue.",
        }
    )
    report = run("overlap.html", tmp_path, client)

    request = client.requests[0]
    assert request.images and request.schema
    assert "id 0" in request.text and "overlap" in request.text

    findings = report.results[0].findings
    overlap = next(f for f in findings if f.check == "overlap")
    assert overlap.severity == Severity.INFO
    assert overlap.details["review"]["verdict"] == "intentional"
    assert overlap.details["original_severity"] == "error"

    visual = next(f for f in findings if f.check == "visual/layout")
    assert visual.severity == Severity.WARNING
    assert visual.rect is not None and visual.marker is not None

    assert report.results[0].review_summary == "One layout issue."
    assert report.llm.calls == 1 and report.llm.input_tokens == 1000
    # Sonnet 5.5: 1000 in at $2/M + 100 out at $10/M
    assert report.llm.cost_usd == pytest.approx(0.003)

    html = write_report(report, tmp_path).read_text(encoding="utf-8")
    assert "looks intentional" in html and "Heading is crammed" in html


def test_review_failure_keeps_the_check_working(tmp_path):
    report = run("overlap.html", tmp_path, FakeClient(error="Couldn't reach the API."))
    result = report.results[0]
    assert result.review_error == "Couldn't reach the API."
    assert any(f.check == "overlap" and f.severity == Severity.ERROR for f in result.findings)


def test_apply_review_ignores_bad_ids_and_tiles():
    finding = Finding(
        check="overlap",
        category="layout",
        severity=Severity.ERROR,
        message="x",
        rect=Rect(x=0, y=0, width=10, height=10),
    )
    tiles = [Tile(png=b"", top=0, height=800), Tile(png=b"", top=800, height=400)]
    new = apply_review(
        {
            "verdicts": [{"id": 5, "verdict": "false_positive", "reason": ""}],
            "issues": [
                {"tile": 9, "severity": "error", "kind": "other", "description": "nope", "box": {}},
                {
                    "tile": 2,
                    "severity": "bogus",
                    "kind": "overlap",
                    "description": "on tile two",
                    "box": {"x": 0, "y": 50, "width": 10, "height": 10},
                },
            ],
        },
        [finding],
        tiles,
        VIEWPORTS["desktop"],
    )
    assert finding.severity == Severity.ERROR
    assert len(new) == 1
    assert new[0].rect.y == 850
    assert new[0].severity == Severity.WARNING


@pytest.mark.parametrize(
    "text",
    ['{"a": 1}', '```json\n{"a": 1}\n```', 'Sure! Here you go: {"a": 1} Hope that helps.'],
)
def test_parse_json(text):
    assert parse_json(text) == {"a": 1}


def test_parse_json_rejects_garbage():
    with pytest.raises(LLMError):
        parse_json("no json here")


def test_solid_png_is_a_png():
    png = _solid_png(4, 4, (255, 0, 0))
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and png.endswith(b"IEND\xaeB`\x82")


def test_vision_probe():
    ok, _ = asyncio.run(_vision_probe(FakeClient({"color": "red"})))
    assert ok
    ok, message = asyncio.run(_vision_probe(FakeClient({"color": "blue"})))
    assert not ok and "blue" in message
