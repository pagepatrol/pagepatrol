from pathlib import Path
from typing import Any

import yaml

HEADER = """\
# Written by PagePatrol. When the AI completes a plain-language step, the
# concrete actions it took are kept here so later runs replay them without
# the AI. Delete an entry (or run with --fresh) to have it redone.
"""


class Recordings:
    """Concrete steps for plain-language steps, by scenario name and step text."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict[str, list[Any]]] = {}
        self.changed = False
        if path.exists():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data = loaded

    def get(self, scenario: str, text: str) -> list[Any] | None:
        steps = self.data.get(scenario, {}).get(text)
        return steps if isinstance(steps, list) and steps else None

    def put(self, scenario: str, text: str, steps: list[Any]) -> None:
        self.data.setdefault(scenario, {})[text] = steps
        self.changed = True

    def save(self) -> None:
        if not self.changed:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = yaml.safe_dump(self.data, sort_keys=False, allow_unicode=True, width=100)
        self.path.write_text(HEADER + body, encoding="utf-8")
        self.changed = False
