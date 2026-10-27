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


class Performance(BaseModel):
    # Times in milliseconds from the start of navigation; None when the
    # browser can't measure it.
    ttfb_ms: float | None = None
    fcp_ms: float | None = None
    lcp_ms: float | None = None
    load_ms: float | None = None
    tbt_ms: float | None = None
    cls: float | None = None
    requests: int = 0
    transfer_bytes: int = 0
    # How the load was slowed down to resemble a phone, or None.
    throttling: str | None = None


class ViewportResult(BaseModel):
    viewport: Viewport
    browser: str = "chromium"
    performance: Performance | None = None
    screenshot: str | None = None
    annotated_screenshot: str | None = None
    findings: list[Finding] = ModelField(default_factory=list)
    console: list[ConsoleMessage] = ModelField(default_factory=list)
    page_title: str | None = None
    review_summary: str | None = None
    review_error: str | None = None


class LLMRun(BaseModel):
    provider: str
    model: str
    depth: str | None = None
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # None when we don't know the model's price.
    cost_usd: float | None = None


class CheckReport(BaseModel):
    target: str
    url: str
    pagepatrol_version: str
    started_at: datetime
    duration_s: float
    results: list[ViewportResult]
    llm: LLMRun | None = None

    def all_findings(self) -> list[Finding]:
        return [f for r in self.results for f in r.findings]

    def label(self, result: "ViewportResult") -> str:
        """The viewport's name, with the browser in front when it isn't only Chromium."""
        if {r.browser for r in self.results} == {"chromium"}:
            return result.viewport.name
        return f"{result.browser} {result.viewport.name}"

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
            merged.viewports.append(report.label(result))
            if g.severity.rank > merged.severity.rank:
                merged.severity = g.severity
    return sorted(groups.values(), key=lambda g: (-g.severity.rank, -g.count))


class StepResult(BaseModel):
    index: int
    action: str
    description: str
    status: str  # passed, failed, skipped
    url: str | None = None
    error: str | None = None
    screenshot: str | None = None
    duration_ms: int = 0
    # How the target was actually found, so the step can be replayed exactly.
    resolved: dict[str, Any] | None = None
    # For plain-language steps: what was actually done, and whether the AI
    # did it ("ai") or a saved recording was replayed ("recording").
    actions: list[str] = ModelField(default_factory=list)
    via: str | None = None
    note: str | None = None
    findings: list[Finding] = ModelField(default_factory=list)


class ScenarioResult(BaseModel):
    name: str
    source: str | None = None
    viewport: Viewport
    browser: str = "chromium"
    status: str  # passed, failed
    steps: list[StepResult] = ModelField(default_factory=list)
    console: list[ConsoleMessage] = ModelField(default_factory=list)

    def all_findings(self) -> list[Finding]:
        return [f for s in self.steps for f in s.findings]


class RunReport(BaseModel):
    base_url: str | None
    pagepatrol_version: str
    started_at: datetime
    duration_s: float
    scenarios: list[ScenarioResult]
    llm: LLMRun | None = None

    def all_findings(self) -> list[Finding]:
        return [f for s in self.scenarios for f in s.all_findings()]

    @property
    def browsers(self) -> list[str]:
        return list(dict.fromkeys(s.browser for s in self.scenarios))
