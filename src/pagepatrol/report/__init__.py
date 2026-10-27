from collections import Counter
from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, FunctionLoader, select_autoescape

from pagepatrol.browsers import NAMES
from pagepatrol.checks.perf import format_bytes, tiles
from pagepatrol.models import CheckReport, RunReport, Severity, group_by_check


def _load(name: str) -> str:
    return files("pagepatrol.report").joinpath(name).read_text(encoding="utf-8")


def _environment() -> Environment:
    env = Environment(
        loader=FunctionLoader(_load),
        autoescape=select_autoescape(default=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["group_by_check"] = group_by_check
    env.globals["verdict_labels"] = {
        "confirmed": "confirmed",
        "intentional": "looks intentional",
        "false_positive": "false alarm",
        "unsure": "not sure",
    }
    return env


def _totals(findings) -> dict[str, int]:
    counts = Counter(f.severity for f in findings)
    return {s.value: counts.get(s, 0) for s in Severity}


def _write(out_dir: Path, template: str, json_text: str, **context) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json_text, encoding="utf-8")
    html = _environment().get_template(template).render(**context)
    path = out_dir / "report.html"
    path.write_text(html, encoding="utf-8")
    return path


def write_report(report: CheckReport, out_dir: Path) -> Path:
    return _write(
        out_dir,
        "check.html",
        report.model_dump_json(indent=2),
        report=report,
        totals=_totals(report.all_findings()),
        perf_tiles=tiles,
        format_bytes=format_bytes,
    )


def write_run_report(report: RunReport, out_dir: Path) -> Path:
    return _write(
        out_dir,
        "run.html",
        report.model_dump_json(indent=2),
        report=report,
        totals=_totals(report.all_findings()),
        passed=sum(1 for s in report.scenarios if s.status == "passed"),
        browser_names=NAMES,
        perf_tiles=tiles,
        format_bytes=format_bytes,
    )
