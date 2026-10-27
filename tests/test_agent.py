import re
from pathlib import Path

import yaml

from pagepatrol import agent
from pagepatrol.config import LLMConfig
from pagepatrol.llm import LLMError, Request, Usage
from pagepatrol.report import write_run_report
from pagepatrol.runner import RunOptions, run_scenarios
from pagepatrol.scenario import load_scenarios

APP = Path(__file__).parent / "fixtures" / "app"
BASE = (APP / "index.html").as_uri()


class ScriptedAI:
    """Stands in for the model: each turn calls `decide` with the request
    and the elements it lists."""

    def __init__(self, decide):
        self.model = "fake-model"
        self.usage = Usage()
        self.decide = decide
        self.requests: list[Request] = []

    async def complete_json(self, request: Request):
        self.requests.append(request)
        self.usage.add(2000, 50)
        answer = self.decide(request.text, len(self.requests))
        if isinstance(answer, Exception):
            raise answer
        return {"element": 0, "value": "", "finished": False, "proof": "", **answer}

    async def list_models(self):
        return ["fake-model"]


def element(text: str, name: str, context: str | None = None) -> int:
    for line in text.splitlines():
        m = re.match(r"\[(\d+)\] \S+ '([^']*)'(.*)", line)
        if m and m[2] == name and (context is None or context in m[3]):
            return int(m[1])
    raise AssertionError(f"{name!r} not in the element list")


def run(tmp_path: Path, text: str, ai=None, **options):
    path = tmp_path / "scenario.yaml"
    path.write_text(text, encoding="utf-8")
    return run_scenarios(
        load_scenarios(path),
        RunOptions(
            out_dir=tmp_path / "out",
            base_url=BASE,
            llm=ai,
            llm_config=LLMConfig("anthropic", "claude-sonnet-5-5"),
            recordings=tmp_path / "recordings.yaml",
            **options,
        ),
    )


CHEAPEST = """
name: Buy something cheap
url: shop.html
steps:
  - Add the cheapest item to the cart
  - expect: "Cart: 1 item"
"""


def cheapest(text, turn):
    if "Added Green cup" in text:
        return {"action": "done", "value": "Added Green cup to your cart"}
    return {"action": "click", "element": element(text, "Add to cart", "Green cup")}


def test_ai_step_is_recorded_then_replayed_without_the_ai(tmp_path):
    ai = ScriptedAI(cheapest)
    first = run(tmp_path, CHEAPEST, ai).scenarios[0]
    step = first.steps[1]

    assert first.status == "passed", [s.error for s in first.steps]
    assert step.via == "ai"
    assert step.actions == [
        "Click button 'Add to cart'",
        "Expect text 'Added Green cup to your cart'",
    ]
    # Three buttons share the name, so the context has to be in the prompt.
    assert "Green cup" in ai.requests[0].text and "$4" in ai.requests[0].text

    saved = yaml.safe_load((tmp_path / "recordings.yaml").read_text(encoding="utf-8"))
    recorded = saved["Buy something cheap"]["Add the cheapest item to the cart"]
    assert recorded[0]["click"]["name"] == "Add to cart"
    assert recorded[0]["click"]["nth"] == 1
    assert recorded[1] == {"expect": "Added Green cup to your cart"}

    second = run(tmp_path, CHEAPEST, ai=None).scenarios[0]
    assert second.status == "passed", [s.error for s in second.steps]
    assert second.steps[1].via == "recording"
    assert len(ai.requests) == 2  # no new calls


def test_form_filling_is_recorded_with_its_values(tmp_path):
    scenario = """
name: Contact
url: contact.html
steps:
  - Send a message to the shop
"""

    def decide(text, turn):
        return [
            {"action": "fill", "element": element(text, "Email"), "value": "test@example.com"},
            {"action": "fill", "element": element(text, "Message"), "value": "Hello"},
            {"action": "click", "element": element(text, "Send")},
            {"action": "done", "value": "Thanks for your message"},
        ][turn - 1]

    ai = ScriptedAI(decide)
    first = run(tmp_path, scenario, ai).scenarios[0]
    assert first.status == "passed", [s.error for s in first.steps]

    recorded = yaml.safe_load((tmp_path / "recordings.yaml").read_text(encoding="utf-8"))
    steps = recorded["Contact"]["Send a message to the shop"]
    assert steps[0]["fill"]["value"] == "test@example.com"

    second = run(tmp_path, scenario).scenarios[0]
    assert second.status == "passed", [s.error for s in second.steps]
    assert second.steps[1].via == "recording"


def test_broken_recording_falls_back_to_the_ai(tmp_path):
    (tmp_path / "recordings.yaml").write_text(
        yaml.safe_dump(
            {"Buy something cheap": {"Add the cheapest item to the cart": [{"click": "Buy now"}]}}
        ),
        encoding="utf-8",
    )
    report = run(tmp_path, CHEAPEST, ScriptedAI(cheapest))
    step = report.scenarios[0].steps[1]
    assert report.scenarios[0].status == "passed"
    assert step.via == "ai"
    assert "stopped working" in step.note

    saved = yaml.safe_load((tmp_path / "recordings.yaml").read_text(encoding="utf-8"))
    assert "click" in saved["Buy something cheap"]["Add the cheapest item to the cart"][0]


def test_broken_recording_without_ai_says_how_to_fix_it(tmp_path):
    (tmp_path / "recordings.yaml").write_text(
        yaml.safe_dump(
            {"Buy something cheap": {"Add the cheapest item to the cart": [{"click": "Buy now"}]}}
        ),
        encoding="utf-8",
    )
    step = run(tmp_path, CHEAPEST).scenarios[0].steps[1]
    assert step.status == "failed"
    assert "no longer work" in step.error and "pagepatrol init" in step.error


def test_plain_step_without_ai_or_recording(tmp_path):
    step = run(tmp_path, CHEAPEST).scenarios[0].steps[1]
    assert step.status == "failed"
    assert "needs an AI model" in step.error


def test_ai_can_give_up(tmp_path):
    ai = ScriptedAI(lambda text, turn: {"action": "fail", "value": "There is no wishlist"})
    report = run(tmp_path, CHEAPEST.replace("Add the cheapest", "Add to wishlist the cheapest"), ai)
    step = report.scenarios[0].steps[1]
    assert step.status == "failed"
    assert "There is no wishlist" in step.error
    assert report.scenarios[0].steps[2].status == "skipped"
    assert not (tmp_path / "recordings.yaml").exists()


def test_mistakes_are_fed_back(tmp_path):
    def decide(text, turn):
        if turn == 1:
            return {"action": "click", "element": 999}
        if turn == 2:
            return {"action": "done", "value": "Order confirmed"}
        return cheapest(text, turn)

    ai = ScriptedAI(decide)
    report = run(tmp_path, CHEAPEST, ai)
    assert report.scenarios[0].status == "passed"
    assert "there is no element 999" in ai.requests[1].text
    assert "'Order confirmed' isn't visible" in ai.requests[2].text


def test_ai_gives_up_after_too_many_actions(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "MAX_ACTIONS", 3)
    ai = ScriptedAI(lambda text, turn: {"action": "hover", "element": element(text, "Add to cart")})
    step = run(tmp_path, CHEAPEST, ai).scenarios[0].steps[1]
    assert step.status == "failed"
    assert "Gave up after 3 actions" in step.error
    assert len(ai.requests) == 3


def test_ai_errors_fail_the_step(tmp_path):
    ai = ScriptedAI(lambda text, turn: LLMError("Anthropic rejected the API key."))
    step = run(tmp_path, CHEAPEST, ai).scenarios[0].steps[1]
    assert step.status == "failed"
    assert "rejected the API key" in step.error


def test_fresh_ignores_recordings(tmp_path):
    ai = ScriptedAI(cheapest)
    run(tmp_path, CHEAPEST, ai)
    report = run(tmp_path, CHEAPEST, ai, fresh=True)
    assert report.scenarios[0].steps[1].via == "ai"
    assert len(ai.requests) == 4


def test_report_shows_ai_steps_and_cost(tmp_path):
    report = run(tmp_path, CHEAPEST, ScriptedAI(cheapest))
    assert report.llm.calls == 2
    assert report.llm.cost_usd is not None
    html = write_run_report(report, tmp_path / "out").read_text(encoding="utf-8")
    assert "AI steps by fake-model" in html
    assert "Added Green cup to your cart" in html


def test_last_action_can_finish_the_goal(tmp_path):
    def decide(text, turn):
        return {
            "action": "click",
            "element": element(text, "Add to cart", "Green cup"),
            "finished": True,
            "proof": "Added Green cup to your cart",
        }

    ai = ScriptedAI(decide)
    step = run(tmp_path, CHEAPEST, ai).scenarios[0].steps[1]
    assert step.status == "passed", step.error
    assert len(ai.requests) == 1
    assert step.actions[-1] == "Expect text 'Added Green cup to your cart'"


def test_wrong_proof_means_another_turn(tmp_path):
    def decide(text, turn):
        if turn == 1:
            return {
                "action": "click",
                "element": element(text, "Add to cart", "Green cup"),
                "finished": True,
                "proof": "Order placed",
            }
        return {"action": "done", "value": "Added Green cup to your cart"}

    ai = ScriptedAI(decide)
    step = run(tmp_path, CHEAPEST, ai).scenarios[0].steps[1]
    assert step.status == "passed", step.error
    assert len(ai.requests) == 2
    assert "'Order placed' to show up" in ai.requests[1].text


def test_a_click_needs_proof_to_finish_early(tmp_path):
    def decide(text, turn):
        if turn == 1:
            return {
                "action": "click",
                "element": element(text, "Add to cart", "Green cup"),
                "finished": True,
            }
        return {"action": "done", "value": ""}

    ai = ScriptedAI(decide)
    run(tmp_path, CHEAPEST, ai)
    assert len(ai.requests) == 2
