import contextlib
import os
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
from pagepatrol.review import Depth
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
    llm: bool | None = typer.Option(
        None,
        "--llm/--no-llm",
        help="AI review of the screenshots. On by default once `pagepatrol init` is done.",
    ),
    depth: Depth = typer.Option(
        Depth.BALANCED,
        "--depth",
        help="How much of the page the AI looks at, and how hard it thinks.",
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
        depth=depth,
    )
    if llm is not False:
        options.llm, options.llm_config = _llm_client(required=llm is True)

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


def _llm_client(required: bool, tip: str = "add an AI review of the screenshots"):
    from pagepatrol.config import load_llm_config
    from pagepatrol.llm import create_client
    from pagepatrol.llm.providers import LLMNotConfigured

    config = load_llm_config()
    if config is None:
        if required:
            console.print("[red]No AI model set up yet. Run: pagepatrol init[/red]")
            raise typer.Exit(2)
        console.print(f"[dim]Tip: run `pagepatrol init` to {tip}.[/dim]")
        return None, None
    try:
        return create_client(config), config
    except LLMNotConfigured as exc:
        if required:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc
        console.print(f"[yellow]Not using AI: {exc}[/yellow]")
        return None, None


@app.command()
def init() -> None:
    """Pick the AI provider and model used to review screenshots. Optional."""
    import asyncio

    from pagepatrol.config import LLMConfig, save_key, save_llm_config
    from pagepatrol.llm import LLMError, create_client
    from pagepatrol.llm.pricing import prices_for
    from pagepatrol.llm.providers import PROVIDERS

    console.print(
        "PagePatrol works without an AI model. Adding one lets it look at the screenshots\n"
        "the way a tester would. Your key stays on this machine.\n"
    )
    providers = list(PROVIDERS.values())
    for i, p in enumerate(providers, 1):
        console.print(f"  {i}. {p.label}")
    choice = typer.prompt("\nProvider", default="1")
    if not choice.isdigit() or not 1 <= int(choice) <= len(providers):
        raise typer.BadParameter(f"Pick a number from 1 to {len(providers)}.")
    provider = providers[int(choice) - 1]

    base_url = None
    if provider.id == "custom":
        base_url = typer.prompt("Server URL (OpenAI-compatible, e.g. http://localhost:1234/v1)")
    elif provider.id == "ollama":
        base_url = typer.prompt("Ollama URL", default=provider.base_url)

    key, store_key = None, False
    env_names = [n for n in ("PAGEPATROL_API_KEY", provider.key_env) if n]
    from_env = next((n for n in env_names if os.environ.get(n)), None)
    if from_env:
        console.print(f"Using the key in ${from_env}.")
        key = os.environ[from_env]
    elif provider.needs_key or provider.id == "custom":
        hint = "" if provider.needs_key else " (leave empty if none)"
        key = (
            typer.prompt(
                "API key" + hint,
                default=None if provider.needs_key else "",
                hide_input=True,
                show_default=False,
            ).strip()
            or None
        )
        store_key = key is not None

    probe = create_client(LLMConfig(provider.id, model="", base_url=base_url), api_key=key)
    models: list[str] = []
    try:
        with console.status("Fetching models..."):
            models = asyncio.run(probe.list_models())
    except LLMError as exc:
        console.print(f"[yellow]{exc}[/yellow]")

    shown = models if len(models) <= 30 else []
    for i, m in enumerate(shown, 1):
        console.print(f"  {i}. {m}")
    if models and not shown:
        console.print(f"{len(models)} models available. Type the id of the one you want.")
    default_model = provider.suggested_model if provider.suggested_model in models else None
    default_model = default_model or (shown[0] if shown else None)
    answer = typer.prompt("Model (number or id)", default=default_model).strip()
    if answer.isdigit() and 1 <= int(answer) <= len(shown):
        answer = shown[int(answer) - 1]

    config = LLMConfig(provider.id, model=answer, base_url=base_url)
    if prices_for(config) is None:
        console.print(
            "\nOptional: the model's price, so reports can show what each check cost.\n"
            "Look it up on your provider's pricing page. Leave empty to skip."
        )
        config.price_input = _ask_price("USD per 1M input tokens")
        config.price_output = _ask_price("USD per 1M output tokens") if config.price_input else None
        if config.price_output is None:
            config.price_input = None
    client = create_client(config, api_key=key)
    with console.status(f"Testing {answer} with an image..."):
        ok, message = asyncio.run(_vision_probe(client))
    console.print(("[green]" if ok else "[yellow]") + message + "[/]")
    if not ok and not typer.confirm("Save this setup anyway?", default=False):
        raise typer.Exit(1)

    path = save_llm_config(config)
    if store_key and key:
        console.print(f"Key saved to {save_key(provider.id, key)}")
    console.print(f"Saved to {path}. `pagepatrol check` will now include an AI review.")


def _ask_price(label: str) -> float | None:
    answer = typer.prompt(label, default="", show_default=False).strip().replace(",", ".")
    if not answer:
        return None
    try:
        return max(0.0, float(answer.lstrip("$")))
    except ValueError:
        console.print("[yellow]Not a number, skipping.[/yellow]")
        return None


async def _vision_probe(client) -> tuple[bool, str]:
    from pagepatrol.llm import Image, LLMError, Request

    request = Request(
        system="You answer briefly.",
        text="What is the main color of the image above? Answer with one lowercase word.",
        images=[Image(png=_solid_png(32, 32, (220, 30, 30)), label="Image:")],
        schema={
            "type": "object",
            "properties": {"color": {"type": "string"}},
            "required": ["color"],
            "additionalProperties": False,
        },
        schema_name="color",
        effort="low",
        max_tokens=2000,
    )
    try:
        data = await client.complete_json(request)
    except LLMError as exc:
        return False, f"Test failed: {exc}"
    color = str(data.get("color", "")).lower()
    if "red" in color:
        return True, "Works, and the model can see images."
    return False, f"The model answered but didn't see the image correctly (said {color!r})."


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + bytes(rgb) * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


@app.command()
def run(
    paths: list[Path] = typer.Argument(
        None, help="Scenario files or folders. Default: .pagepatrol/scenarios"
    ),
    base_url: str | None = typer.Option(
        None, "--base-url", "-b", help="Where relative scenario URLs point, e.g. localhost:3000."
    ),
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Where to write the report. Default: .pagepatrol/runs/<time>"
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Don't ask before clicking things like Delete or Pay on non-local sites.",
    ),
    login_state: Path | None = typer.Option(
        None,
        "--login-state",
        help="Saved login from `pagepatrol login`. Default: .pagepatrol/auth.json if it exists.",
    ),
    headed: bool = typer.Option(False, "--headed", help="Show the browser while it runs."),
    fail_on: FailOn = typer.Option(
        FailOn.ERROR,
        "--fail-on",
        help="Also exit with code 1 when findings of this level exist. Failed scenarios always do.",
    ),
    open_report: bool = typer.Option(
        False, "--open", help="Open the report in a browser when done."
    ),
    llm: bool | None = typer.Option(
        None,
        "--llm/--no-llm",
        help="Let the AI do plain-language steps that have no recording yet.",
    ),
    fresh: bool = typer.Option(
        False, "--fresh", help="Ignore recorded steps and have the AI redo plain-language steps."
    ),
) -> None:
    """Run scenarios step by step, checking the page after every step."""
    from pagepatrol.check import BrowserNotInstalled
    from pagepatrol.report import write_run_report
    from pagepatrol.runner import RunOptions, run_scenarios
    from pagepatrol.scenario import ScenarioError, load_scenarios
    from pagepatrol.targets import resolve_target

    paths = paths or [Path(".pagepatrol") / "scenarios"]
    try:
        scenarios = [s for p in paths for s in load_scenarios(p)]
        base = resolve_target(base_url) if base_url else None
    except (ScenarioError, TargetError, FileNotFoundError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    default_state = Path(".pagepatrol") / "auth.json"
    if login_state is None and default_state.exists():
        login_state = default_state
        console.print(f"[dim]Using the saved login in {default_state}[/dim]")

    out_dir = out or Path(".pagepatrol") / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    options = RunOptions(
        out_dir=out_dir,
        base_url=base,
        storage_state=login_state,
        confirm=None if yes else _confirm_destructive,
        headed=headed,
        recordings=Path(".pagepatrol") / "recordings.yaml",
        fresh=fresh,
    )
    plain = any(step.action == "do" for s in scenarios for step in s.steps)
    if plain and llm is not False:
        options.llm, options.llm_config = _llm_client(
            required=llm is True or fresh, tip="let the AI do plain-language steps"
        )
    try:
        with console.status(f"Running {len(scenarios)} scenario(s)..."):
            report = run_scenarios(scenarios, options)
    except BrowserNotInstalled as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    report_path = write_run_report(report, out_dir)
    _print_run_summary(report)
    console.print(f"\nReport: [bold]{report_path.resolve()}[/bold]", soft_wrap=True)
    if open_report:
        webbrowser.open(report_path.resolve().as_uri())

    failed = any(s.status == "failed" for s in report.scenarios)
    threshold = {FailOn.ERROR: Severity.ERROR.rank, FailOn.WARNING: Severity.WARNING.rank}.get(
        fail_on
    )
    too_many = threshold is not None and any(
        f.severity.rank >= threshold for f in report.all_findings()
    )
    if failed or too_many:
        raise typer.Exit(1)


def _confirm_destructive(name: str, url: str) -> bool:
    if not sys.stdin.isatty():
        return False
    console.print()
    return typer.confirm(
        f"About to click {name!r} on {url}. This may not be undoable. Go ahead?", default=False
    )


def _print_run_summary(report, labels: tuple[str, str] = ("PASS", "FAIL")) -> None:
    console.print()
    width = max(len(label) for label in labels)
    for s in report.scenarios:
        ok, bad = (label.ljust(width) for label in labels)
        mark = f"[green]{ok}[/]" if s.status == "passed" else f"[red]{bad}[/]"
        done = sum(1 for step in s.steps if step.status == "passed")
        console.print(f"{mark}  {s.name} [dim]({done}/{len(s.steps)} steps)[/dim]")
        failed = next((step for step in s.steps if step.status == "failed"), None)
        if failed:
            console.print(f"      step {failed.index + 1}: {failed.description}")
            console.print(f"      [red]{failed.error}[/red]", soft_wrap=True)
        elif s.status == "failed":
            for step in s.steps:
                error = next((f for f in step.findings if f.severity == Severity.ERROR), None)
                if error:
                    console.print(
                        f"      {step.description}: [red]{error.message}[/red]", soft_wrap=True
                    )
    findings = report.all_findings()
    counts = {sev: sum(1 for f in findings if f.severity == sev) for sev in Severity}
    parts = [f"[{SEVERITY_STYLE[sev]}]{counts[sev]} {sev.value}[/]" for sev in Severity]
    console.print("\nFindings along the way: " + "  ".join(parts))
    console.print(f"[dim]Took {report.duration_s:.0f}s[/dim]")
    if report.llm:
        llm = report.llm
        console.print(
            f"[dim]AI steps: {llm.model}, {llm.calls} call(s), "
            f"{llm.input_tokens:,} in / {llm.output_tokens:,} out tokens, "
            f"{format_cost(llm.cost_usd)}[/dim]"
        )
    replayed = sum(1 for s in report.scenarios for step in s.steps if step.via == "recording")
    if replayed:
        console.print(f"[dim]Replayed {replayed} recorded step(s) without the AI.[/dim]")


@app.command()
def plan(
    project: Path = typer.Argument(Path("."), help="The project folder to read."),
    out: Path = typer.Option(
        Path(".pagepatrol") / "scenarios", "--out", "-o", help="Where to save the scenarios."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Don't ask before sending code or saving scenarios."
    ),
) -> None:
    """Read a project's code and suggest scenarios to test. Needs an AI model."""
    import asyncio

    from pagepatrol.llm import LLMError
    from pagepatrol.llm.pricing import estimate_cost, prices_for
    from pagepatrol.planner import (
        EXPECTED_OUTPUT_TOKENS,
        collect_sources,
        estimate_input_tokens,
        make_plan,
        write_scenarios,
    )
    from pagepatrol.scenario import ScenarioError, load_scenarios

    if not project.is_dir():
        console.print(f"[red]{project} is not a folder.[/red]")
        raise typer.Exit(2)
    client, config = _llm_client(required=True)

    with console.status("Reading the project..."):
        sources = collect_sources(project)
    if not sources.files:
        console.print("[red]Found no web source files to read in this folder.[/red]")
        raise typer.Exit(2)

    tokens = estimate_input_tokens(sources)
    cost = estimate_cost(prices_for(config), tokens, EXPECTED_OUTPUT_TOKENS)
    console.print(
        f"Will send {len(sources.files)} file(s), about {tokens:,} tokens, "
        f"to {config.model} on {config.provider}. {format_cost(cost).capitalize()}."
    )
    if sources.left_out:
        console.print(f"[dim]{sources.left_out} more file(s) didn't fit and are left out.[/dim]")
    if sources.secret_files:
        console.print(f"[dim]Skipped {sources.secret_files} file(s) that may hold secrets.[/dim]")
    if sources.redacted:
        console.print(f"[dim]Masked {sources.redacted} value(s) that look like secrets.[/dim]")
    if not yes and not typer.confirm("Send?", default=True):
        raise typer.Exit(1)

    existing: list[str] = []
    if out.is_dir():
        with contextlib.suppress(ScenarioError):
            existing = [s.name for s in load_scenarios(out)]

    try:
        with console.status(f"{config.model} is reading the code..."):
            result = asyncio.run(make_plan(client, sources, existing))
    except LLMError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    usage = client.usage
    spent = estimate_cost(prices_for(config), usage.input_tokens, usage.output_tokens)
    console.print(
        f"[dim]{usage.input_tokens:,} in / {usage.output_tokens:,} out tokens, "
        f"{format_cost(spent)}[/dim]"
    )
    for problem in result.dropped:
        console.print(f"[yellow]Left out an invalid scenario: {problem}[/yellow]")
    if not result.scenarios:
        console.print("No scenarios suggested." + (f" {result.notes}" if result.notes else ""))
        raise typer.Exit(1)

    for i, scenario in enumerate(result.scenarios, 1):
        login_note = " [yellow](needs login)[/yellow]" if scenario.needs_login else ""
        console.print(f"\n[bold]{i}. {scenario.name}[/bold]{login_note}  [dim]{scenario.url}[/dim]")
        if scenario.why:
            console.print(f"   [dim]{scenario.why}[/dim]", soft_wrap=True)
        for step in scenario.steps:
            text = step if isinstance(step, str) else " ".join(f"{k}: {v}" for k, v in step.items())
            console.print(f"   - {text}", soft_wrap=True)
    if result.notes:
        console.print(f"\n[dim]{result.notes}[/dim]", soft_wrap=True)

    chosen = result.scenarios
    if not yes:
        answer = (
            typer.prompt("\nSave which? (all, none, or numbers like 1,3)", default="all")
            .strip()
            .lower()
        )
        chosen = _pick(answer, result.scenarios)
    if not chosen:
        console.print("Nothing saved.")
        return

    for path in write_scenarios(chosen, out):
        console.print(f"Saved {path}")
    console.print(
        "\nRun them with: [bold]pagepatrol run --base-url <your site>[/bold]\n"
        "[dim]The AI does the plain-language steps the first time and records them; "
        "later runs replay without it.[/dim]"
    )


def _pick(answer: str, items: list) -> list:
    if answer in ("", "all", "a", "y", "yes"):
        return items
    if answer in ("none", "n", "no"):
        return []
    picked = []
    for part in answer.replace(" ", ",").split(","):
        if part.isdigit() and 1 <= int(part) <= len(items) and items[int(part) - 1] not in picked:
            picked.append(items[int(part) - 1])
    return picked


@app.command()
def explore(
    url: str = typer.Argument(..., help="Where to start, e.g. localhost:3000."),
    max_pages: int = typer.Option(15, "--max-pages", help="Stop after this many pages."),
    interact: bool | None = typer.Option(
        None,
        "--interact/--no-interact",
        help="Click buttons and send forms with test data. On by default only for "
        "localhost and local files.",
    ),
    viewport: str = typer.Option(
        "desktop", "--viewport", "-v", help="mobile, tablet, desktop or WxH."
    ),
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Where to write the report. Default: .pagepatrol/runs/<time>"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Don't ask before clicking things that look permanent."
    ),
    login_state: Path | None = typer.Option(
        None,
        "--login-state",
        help="Saved login from `pagepatrol login`. Default: .pagepatrol/auth.json if it exists.",
    ),
    headed: bool = typer.Option(False, "--headed", help="Show the browser while it runs."),
    fail_on: FailOn = typer.Option(
        FailOn.ERROR, "--fail-on", help="Exit with code 1 when findings of this level exist."
    ),
    open_report: bool = typer.Option(
        False, "--open", help="Open the report in a browser when done."
    ),
) -> None:
    """Wander through a site without a script: follow links, press buttons, fill forms."""
    from pagepatrol.check import BrowserNotInstalled
    from pagepatrol.explore import ExploreOptions
    from pagepatrol.explore import explore as run_explore
    from pagepatrol.report import write_run_report
    from pagepatrol.runner import is_local
    from pagepatrol.targets import resolve_target

    try:
        start = resolve_target(url)
        vp = parse_viewport(viewport)
    except TargetError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    default_state = Path(".pagepatrol") / "auth.json"
    if login_state is None and default_state.exists():
        login_state = default_state
        console.print(f"[dim]Using the saved login in {default_state}[/dim]")
    if interact is None and not is_local(start):
        console.print(
            "[dim]Only following links on this site. Pass --interact to also press "
            "buttons and send forms with test data.[/dim]"
        )

    out_dir = out or Path(".pagepatrol") / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    options = ExploreOptions(
        out_dir=out_dir,
        viewport=vp,
        max_pages=max_pages,
        interact=interact,
        confirm=None if yes else _confirm_destructive,
        storage_state=login_state,
        headed=headed,
    )
    try:
        with console.status(f"Exploring {start}..."):
            report = run_explore(start, options)
    except BrowserNotInstalled as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    report_path = write_run_report(report, out_dir)
    _print_run_summary(report, labels=("ok", "issues"))
    console.print(f"[dim]Visited {len(report.scenarios)} page(s).[/dim]")
    console.print(f"\nReport: [bold]{report_path.resolve()}[/bold]", soft_wrap=True)
    if open_report:
        webbrowser.open(report_path.resolve().as_uri())

    threshold = {FailOn.ERROR: Severity.ERROR.rank, FailOn.WARNING: Severity.WARNING.rank}.get(
        fail_on
    )
    if threshold is not None and any(f.severity.rank >= threshold for f in report.all_findings()):
        raise typer.Exit(1)


@app.command()
def login(
    url: str = typer.Argument(..., help="The page where you log in."),
    save: Path = typer.Option(
        Path(".pagepatrol") / "auth.json", "--save", help="Where to keep the session."
    ),
) -> None:
    """Open a browser so you can log in, then save the session for `pagepatrol run`."""
    import asyncio

    from playwright.async_api import async_playwright

    from pagepatrol.targets import resolve_target

    async def capture() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(resolve_target(url))
            console.print("Log in in the browser window, then come back here and press Enter.")
            await asyncio.to_thread(input)
            save.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(save))
            await browser.close()

    asyncio.run(capture())
    console.print(f"Saved the session to {save}. `pagepatrol run` will use it.")
    console.print(
        "[yellow]This file holds your login cookies. Don't commit it: "
        "add .pagepatrol/ to your .gitignore.[/yellow]"
    )


@app.command("config")
def show_config() -> None:
    """Show the AI model setup."""
    from pagepatrol.config import key_location, load_llm_config
    from pagepatrol.llm.providers import PROVIDERS

    config = load_llm_config()
    if config is None:
        console.print("No AI model set up. Checks run without one; `pagepatrol init` adds one.")
        return
    provider = PROVIDERS.get(config.provider)
    console.print(f"provider  {config.provider}")
    console.print(f"model     {config.model}")
    if config.base_url:
        console.print(f"base url  {config.base_url}")
    from pagepatrol.llm.pricing import prices_for

    prices = prices_for(config)
    if prices:
        console.print(f"price     ${prices[0]:g} in / ${prices[1]:g} out per 1M tokens")
    else:
        console.print("price     unknown")
    env_names = ["PAGEPATROL_API_KEY", provider.key_env if provider else None]
    env = next((n for n in env_names if n and os.environ.get(n)), None)
    if env:
        console.print(f"key       from ${env}")
    elif location := key_location(config.provider):
        console.print(f"key       saved in {location}")
    else:
        console.print("key       none")


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


def format_cost(cost: float | None) -> str:
    if cost is None:
        return "cost unknown (set prices with `pagepatrol init`)"
    if cost == 0:
        return "free"
    return f"about ${cost:.3f}" if cost < 0.1 else f"about ${cost:.2f}"


def _print_summary(report: CheckReport, max_rows: int = 20) -> None:
    console.print()
    for result in report.results:
        vp = result.viewport
        counts = {s: sum(1 for f in result.findings if f.severity == s) for s in Severity}
        parts = [f"[{SEVERITY_STYLE[s]}]{counts[s]} {s.value}[/]" for s in Severity]
        console.print(f"[bold]{vp.name:<8}[/bold] {vp.width}x{vp.height:<6} " + "  ".join(parts))

    console.print(f"[dim]Took {report.duration_s:.0f}s[/dim]")
    if report.llm:
        llm = report.llm
        console.print(
            f"[dim]AI review: {llm.model} ({llm.depth}), {llm.calls} call(s), "
            f"{llm.input_tokens:,} in / {llm.output_tokens:,} out tokens, "
            f"{format_cost(llm.cost_usd)}[/dim]"
        )
        for result in report.results:
            if result.review_error:
                name = result.viewport.name
                console.print(f"[yellow]AI review failed on {name}: {result.review_error}[/]")

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
