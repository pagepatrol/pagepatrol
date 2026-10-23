from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from pydantic import Field as ModelField


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"error": 2, "warning": 1, "info": 0}[self.value]


class Rect(BaseModel):
    x: float
    y: float
    width: float
    height: float


class Finding(BaseModel):
    check: str
    category: str
    severity: Severity
    message: str
    selector: str | None = None
    rect: Rect | None = None
    # Number drawn on the annotated screenshot, if the finding has a rect.
    marker: int | None = None
    details: dict[str, Any] = ModelField(default_factory=dict)


class ConsoleMessage(BaseModel):
    type: str
    text: str
    location: str | None = None


class Viewport(BaseModel):
    name: str
    width: int
    height: int


class ViewportResult(BaseModel):
    viewport: Viewport
    screenshot: str | None = None
    annotated_screenshot: str | None = None
    findings: list[Finding] = ModelField(default_factory=list)
    console: list[ConsoleMessage] = ModelField(default_factory=list)
    page_title: str | None = None


class CheckReport(BaseModel):
    target: str
    url: str
    pagepatrol_version: str
    started_at: datetime
    duration_s: float
    results: list[ViewportResult]

    def all_findings(self) -> list[Finding]:
        return [f for r in self.results for f in r.findings]

    def count(self, severity: Severity) -> int:
        return sum(1 for f in self.all_findings() if f.severity == severity)


@dataclass
class FindingGroup:
    check: str
    severity: Severity
    findings: list[Finding] = field(default_factory=list)
    viewports: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.findings)

    @property
    def example(self) -> str:
        return self.findings[0].message


def group_by_check(findings: list[Finding]) -> list[FindingGroup]:
    groups: dict[str, FindingGroup] = {}
    for f in findings:
        group = groups.setdefault(f.check, FindingGroup(check=f.check, severity=f.severity))
        group.findings.append(f)
        if f.severity.rank > group.severity.rank:
            group.severity = f.severity
    return sorted(groups.values(), key=lambda g: (-g.severity.rank, -g.count))


def group_findings(report: CheckReport) -> list[FindingGroup]:
    """Group findings by check across all viewports."""
    groups: dict[str, FindingGroup] = {}
    for result in report.results:
        for g in group_by_check(result.findings):
            merged = groups.setdefault(g.check, FindingGroup(check=g.check, severity=g.severity))
            merged.findings += g.findings
            merged.viewports.append(result.viewport.name)
            if g.severity.rank > merged.severity.rank:
                merged.severity = g.severity
    return sorted(groups.values(), key=lambda g: (-g.severity.rank, -g.count))
