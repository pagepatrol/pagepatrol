from collections import Counter
from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, select_autoescape

from pagepatrol.models import CheckReport, Severity, group_by_check


def _environment() -> Environment:
    env = Environment(
        autoescape=select_autoescape(default=True), trim_blocks=True, lstrip_blocks=True
    )
    env.globals["group_by_check"] = group_by_check
    return env


def write_report(report: CheckReport, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")

    template_source = (
        files("pagepatrol.report").joinpath("template.html").read_text(encoding="utf-8")
    )
    template = _environment().from_string(template_source)
    totals = Counter(f.severity for f in report.all_findings())
    html = template.render(
        report=report,
        totals={s.value: totals.get(s, 0) for s in Severity},
    )
    path = out_dir / "report.html"
    path.write_text(html, encoding="utf-8")
    return path
