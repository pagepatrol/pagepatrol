from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ACTIONS = {
    "goto",
    "click",
    "fill",
    "select",
    "check",
    "uncheck",
    "press",
    "hover",
    "wait",
    "expect",
    "do",
}

# Keys that can identify an element instead of a plain description.
TARGET_KEYS = ("role", "label", "placeholder", "text", "css", "testid")


class ScenarioError(ValueError):
    pass


@dataclass
class Step:
    action: str
    # The element the step acts on: a plain description ("Send") or a dict
    # with one of TARGET_KEYS (plus "name" for roles, "nth" to pick one).
    target: str | dict[str, Any] | None = None
    value: Any = None
    raw: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        target = self.target if isinstance(self.target, str) else _describe_target(self.target)
        if self.action == "goto":
            return f"Go to {self.value}"
        if self.action == "fill":
            return f"Type {self.value!r} into {target}"
        if self.action == "select":
            return f"Select {self.value!r} in {target}"
        if self.action == "press":
            return f"Press {self.value}" + (f" in {target}" if target else "")
        if self.action == "wait":
            return f"Wait for {_describe_condition(self.value)}"
        if self.action == "expect":
            return f"Expect {_describe_condition(self.value)}"
        if self.action == "do":
            return str(self.value)
        return f"{self.action.capitalize()} {target}"


@dataclass
class Scenario:
    name: str
    steps: list[Step]
    url: str | None = None
    viewport: str = "desktop"
    source: Path | None = None


def _describe_target(target: dict[str, Any] | None) -> str:
    if not target:
        return ""
    for key in TARGET_KEYS:
        if key in target:
            if key == "role":
                return f"{target['role']} {target.get('name', '')!r}".strip()
            return f"{key} {target[key]!r}"
    return str(target)


def _describe_condition(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value} ms"
    if isinstance(value, str):
        return f"text {value!r}"
    if isinstance(value, dict):
        key, val = next(iter(value.items()))
        if key in ("visible", "hidden"):
            target = val if isinstance(val, str) else _describe_target(val)
            return f"{target} to be {key}"
        return f"{key} {val!r}"
    return str(value)


def _parse_target(value: Any, where: str) -> str | dict[str, Any]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict) and any(k in value for k in TARGET_KEYS):
        return value
    raise ScenarioError(f"{where}: expected an element description or one of {TARGET_KEYS}")


def parse_step(raw: Any, where: str) -> Step:
    if isinstance(raw, str):
        # A bare string is a natural-language step for the AI.
        return Step(action="do", value=raw, raw={"do": raw})
    if not isinstance(raw, dict) or len(raw) != 1:
        raise ScenarioError(f"{where}: each step needs exactly one action, like `click: Send`")

    action, arg = next(iter(raw.items()))
    if action not in ACTIONS:
        raise ScenarioError(
            f"{where}: unknown action {action!r}. Known: {', '.join(sorted(ACTIONS))}"
        )

    if action in ("click", "check", "uncheck", "hover"):
        return Step(action, target=_parse_target(arg, where), raw=raw)
    if action in ("fill", "select"):
        if not isinstance(arg, dict) or "value" not in arg:
            raise ScenarioError(f"{where}: `{action}` needs a target and a value")
        target = {k: v for k, v in arg.items() if k != "value"}
        target = target.get("target", target) if "target" in target else target
        return Step(action, target=_parse_target(target, where), value=arg["value"], raw=raw)
    if action == "press":
        if isinstance(arg, str):
            return Step(action, value=arg, raw=raw)
        if isinstance(arg, dict) and "key" in arg:
            target = arg.get("target")
            return Step(
                action,
                target=_parse_target(target, where) if target else None,
                value=arg["key"],
                raw=raw,
            )
        raise ScenarioError(f"{where}: `press` needs a key, like `press: Enter`")
    if action == "goto":
        if not isinstance(arg, str):
            raise ScenarioError(f"{where}: `goto` needs a URL or path")
        return Step(action, value=arg, raw=raw)
    if action in ("wait", "expect"):
        if action == "wait" and isinstance(arg, (int, float)):
            return Step(action, value=arg, raw=raw)
        if isinstance(arg, str):
            return Step(action, value={"text": arg}, raw=raw)
        if isinstance(arg, dict) and len(arg) == 1:
            key = next(iter(arg))
            if key in ("text", "url", "title", "visible", "hidden", "no_text"):
                return Step(action, value=arg, raw=raw)
        raise ScenarioError(
            f"{where}: `{action}` takes text, url, title, visible, hidden or no_text"
        )
    # do
    if not isinstance(arg, str) or not arg.strip():
        raise ScenarioError(f"{where}: `do` needs a sentence describing what to do")
    return Step(action, value=arg.strip(), raw=raw)


def parse_scenario(data: Any, where: str, source: Path | None = None) -> Scenario:
    if not isinstance(data, dict):
        raise ScenarioError(f"{where}: a scenario must be a mapping with name and steps")
    name = data.get("name") or (source.stem if source else "Scenario")
    steps_raw = data.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ScenarioError(f"{where}: `steps` must be a non-empty list")
    steps = [parse_step(s, f"{where}, step {i + 1}") for i, s in enumerate(steps_raw)]
    return Scenario(
        name=str(name),
        steps=steps,
        url=data.get("url"),
        viewport=str(data.get("viewport", "desktop")),
        source=source,
    )


def load_scenarios(path: Path) -> list[Scenario]:
    files = sorted(path.rglob("*.y*ml")) if path.is_dir() else [path]
    scenarios = []
    for file in files:
        if file.suffix not in (".yaml", ".yml"):
            continue
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ScenarioError(f"{file}: not valid YAML: {exc}") from exc
        items = data.get("scenarios") if isinstance(data, dict) and "scenarios" in data else [data]
        for i, item in enumerate(items):
            where = f"{file.name}" + (f" #{i + 1}" if len(items) > 1 else "")
            scenarios.append(parse_scenario(item, where, file))
    if not scenarios:
        raise ScenarioError(f"No scenario files (*.yaml) found in {path}")
    return scenarios
