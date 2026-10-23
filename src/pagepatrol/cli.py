import subprocess
import sys
import webbrowser
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from pagepatrol import __version__
from pagepatrol.models import CheckReport, Severity, group_findings
from pagepatrol.targets import TargetError, parse_viewport

app = typer.Typer(
    name="pagepatrol",
    help="Open-source, local-first QA agent for web pages.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

SEVERITY_STYLE = {Severity.ERROR: "red", Severity.WARNING: "yellow", Severity.INFO: "blue"}


class FailOn(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    NEVER = "never"


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"pagepatrol {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_print_version,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    pass


@app.command()
def check(
    target: str = typer.Argument(..., help="URL, HTML file or folder with an index.html."),
    viewport: list[str] = typer.Option(
        ["mobile", "desktop"],
        "--viewport",
        "-v",
        help="mobile, tablet, desktop or WIDTHxHEIGHT. Repeat for several.",
    ),
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Where to write the report. Default: .pagepatrol/runs/<time>"
    ),
    links: bool = typer.Option(
        True, "--links/--no-links", help="Check that links on the page work."
    ),
    external_links: bool = typer.Option(
        False, "--external-links", help="Also check links to other sites."
    ),
    fail_on: FailOn = typer.Option(
        FailOn.ERROR, "--fail-on", help="Exit with code 1 when findings of this level exist."
    ),
    open_report: bool = typer.Option(
        False, "--open", help="Open the report in a browser when done."
    ),
) -> None:
    """Check a single page for layout, readability, accessibility and runtime problems."""
    from pagepatrol.check import BrowserNotInstalled, CheckOptions, PageLoadError, run_check
    from pagepatrol.report import write_report

    try:
        viewports = [parse_viewport(v) for v in viewport]
    except TargetError as exc:
        raise typer.BadParameter(str(exc), param_hint="--viewport") from exc

    out_dir = out or Path(".pagepatrol") / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    options = CheckOptions(
        viewports=viewports,
        out_dir=out_dir,
        check_links=links,
        external_links=external_links,
    )

    try:
        with console.status(f"Checking {target}..."):
            report = run_check(target, options)
    except (TargetError, PageLoadError, BrowserNotInstalled) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    report_path = write_report(report, out_dir)
    _print_summary(report)
    console.print(f"\nReport: [bold]{report_path.resolve()}[/bold]", soft_wrap=True)
    if open_report:
        webbrowser.open(report_path.resolve().as_uri())

    threshold = {FailOn.ERROR: Severity.ERROR.rank, FailOn.WARNING: Severity.WARNING.rank}.get(
        fail_on
    )
    if threshold is not None and any(f.severity.rank >= threshold for f in report.all_findings()):
        raise typer.Exit(1)


@app.command()
def install(
    with_deps: bool = typer.Option(
        False, "--with-deps", help="Also install system libraries Chromium needs (Linux)."
    ),
) -> None:
    """Download the Chromium build PagePatrol uses."""
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    if with_deps:
        cmd.append("--with-deps")
    raise typer.Exit(subprocess.call(cmd))


def _print_summary(report: CheckReport, max_rows: int = 20) -> None:
    console.print()
    for result in report.results:
        vp = result.viewport
        counts = {s: sum(1 for f in result.findings if f.severity == s) for s in Severity}
        parts = [f"[{SEVERITY_STYLE[s]}]{counts[s]} {s.value}[/]" for s in Severity]
        console.print(f"[bold]{vp.name:<8}[/bold] {vp.width}x{vp.height:<6} " + "  ".join(parts))

    groups = group_findings(report)
    if not groups:
        console.print("\n[green]No issues found.[/green]")
        return

    table = Table(box=None, padding=(0, 1), show_edge=False, header_style="dim")
    table.add_column("level", no_wrap=True)
    table.add_column("check", no_wrap=True)
    table.add_column("count", justify="right", no_wrap=True)
    table.add_column("example", ratio=1)
    table.add_column("where", style="dim", no_wrap=True)
    for g in groups[:max_rows]:
        style = SEVERITY_STYLE[g.severity]
        table.add_row(
            f"[{style}]{g.severity.value}[/]",
            g.check,
            str(g.count),
            g.example if len(g.example) <= 70 else g.example[:67] + "...",
            ", ".join(g.viewports),
        )
    console.print()
    console.print(table)
    if len(groups) > max_rows:
        console.print(
            f"[dim]...and {len(groups) - max_rows} more kinds of issue in the report[/dim]"
        )
