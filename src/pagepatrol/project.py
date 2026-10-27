import contextlib
import functools
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CONFIG = Path(".pagepatrol") / "config.toml"
READY_TIMEOUT_S = 120
# How long to wait for a dev server to print its address before trying the usual port.
GUESS_AFTER_S = 15

MARKERS = ("package.json", "manage.py", "pyproject.toml", "requirements.txt", "index.html")
NPM_SCRIPTS = ("dev", "start", "serve")
DEFAULT_PORTS = {"vite": 5173, "astro": 4321, "@angular/core": 4200}

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PRINTED_URL = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1?\]):\d{2,5}[^\s'\")]*", re.IGNORECASE
)
PRINTED_PORT = re.compile(
    r"\b(?:listening|running|started|serving)\b.*?\bport\b\s*:?\s*(\d{2,5})", re.IGNORECASE
)


class ProjectError(Exception):
    pass


@dataclass
class SetupStep:
    description: str
    command: list[str]
    cwd: Path

    def shell_command(self) -> str:
        return _join(self.command)


@dataclass
class ServerSpec:
    label: str
    cwd: Path
    # None means serve the folder as static files from inside PagePatrol.
    command: list[str] | str | None
    url: str | None = None
    guess_url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout: float = READY_TIMEOUT_S
    # What has to happen before the server can start, like installing packages.
    setup: list[SetupStep] = field(default_factory=list)

    def shell_command(self) -> str:
        if isinstance(self.command, str):
            return self.command
        return _join(self.command or [])


def _join(command: list[str]) -> str:
    if sys.platform == "win32":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def is_project(folder: Path) -> bool:
    return folder.is_dir() and (
        (folder / CONFIG).is_file()
        or any((folder / m).exists() for m in MARKERS)
        or any(folder.glob("*.csproj"))
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load_config(project: Path) -> dict:
    path = project / CONFIG
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")).get("server", {})
    except tomllib.TOMLDecodeError as exc:
        raise ProjectError(f"Can't read {path}: {exc}") from exc


def save_config(project: Path, command: str, url: str | None) -> Path:
    path = project / CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    # JSON strings are valid TOML basic strings.
    lines = ["[server]", f"command = {json.dumps(command)}"]
    if url:
        lines.append(f"url = {json.dumps(url)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def detect(project: Path, command: str | None = None, url: str | None = None) -> ServerSpec:
    """Work out how to start the project's dev server, or say why we can't."""
    project = project.resolve()
    if not command:
        config = load_config(project)
        command = config.get("command")
        url = url or config.get("url")
        if command:
            spec = ServerSpec(f"{command} (from {CONFIG.as_posix()})", project, command, url=url)
            spec.timeout = float(config.get("timeout", READY_TIMEOUT_S))
            return spec
    if command:
        return ServerSpec(command, project, command, url=url)

    port = free_port()
    for finder in (_django, _npm, _python_app, _dotnet, _static):
        spec = finder(project, port)
        if spec:
            return spec
    raise ProjectError(
        f"Don't know how to start {project.name}. Tell PagePatrol with "
        '--start "your dev command" (and --base-url if it doesn\'t print its address).'
    )


def _venv_python(project: Path) -> str | None:
    for venv in (".venv", "venv", "env"):
        for exe in ("Scripts/python.exe", "bin/python"):
            if (project / venv / exe).is_file():
                return str(project / venv / exe)
    return None


def _can_import(python: str, modules: list[str]) -> bool:
    try:
        done = subprocess.run(
            [python, "-c", "import " + ", ".join(modules)], capture_output=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def _python(project: Path, modules: list[str]) -> tuple[str, list[SetupStep]]:
    """The interpreter to run the app with, and what to install first if anything."""
    venv = _venv_python(project)
    if venv and _can_import(venv, modules):
        return venv, []
    system = "python" if sys.platform == "win32" else "python3"
    if not venv and _can_import(system, modules):
        return system, []

    exe = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    python = venv or str(project / ".venv" / exe)
    if (project / "uv.lock").is_file() and shutil.which("uv"):
        return python, [SetupStep("Install the packages from uv.lock", ["uv", "sync"], project)]

    steps = []
    if not venv:
        steps.append(
            SetupStep(
                "Create a separate Python environment for the project in .venv",
                [sys.executable, "-m", "venv", ".venv"],
                project,
            )
        )
    # Setup runs inside the project, so a relative path is enough and easier to read.
    pip = [os.path.relpath(python, project), "-m", "pip", "install"]
    listed = ""
    if (project / "requirements.txt").is_file():
        listed = (project / "requirements.txt").read_text(encoding="utf-8", errors="replace")
        steps.append(
            SetupStep(
                "Install the packages in requirements.txt",
                [*pip, "-r", "requirements.txt"],
                project,
            )
        )
    elif (project / "pyproject.toml").is_file():
        listed = (project / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        steps.append(SetupStep("Install the project and its packages", [*pip, "-e", "."], project))
    missing = [m for m in modules if m not in listed.lower()]
    if missing:
        steps.append(SetupStep(f"Install {', '.join(missing)}", [*pip, *missing], project))
    return python, steps


def _django(project: Path, port: int) -> ServerSpec | None:
    manage = next(
        (p for p in [project / "manage.py", *project.glob("*/manage.py")] if p.is_file()), None
    )
    if not manage:
        return None
    address = f"127.0.0.1:{port}"
    python, setup = _python(project, ["django"])
    command = [python, "manage.py", "runserver", address, "--noreload"]
    return ServerSpec("Django", manage.parent, command, url=f"http://{address}/", setup=setup)


def _npm(project: Path, port: int) -> ServerSpec | None:
    package = project / "package.json"
    if not package.is_file():
        return None
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ProjectError(f"Can't read {package}: {exc}") from exc
    scripts = data.get("scripts") or {}
    script = next((s for s in NPM_SCRIPTS if s in scripts), None)
    if not script:
        return None
    tool = "npm"
    for lock, name in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lock", "bun")):
        if (project / lock).exists() or (project / lock.replace(".lock", ".lockb")).exists():
            tool = name
            break
    if not shutil.which(tool):
        raise ProjectError(_NEEDS.get(tool, f"This project needs {tool}, which isn't installed."))
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    setup = []
    if deps and not (project / "node_modules").is_dir():
        install = "ci" if tool == "npm" and (project / "package-lock.json").is_file() else "install"
        setup.append(
            SetupStep(
                "Download the packages listed in package.json into node_modules",
                [tool, install],
                project,
            )
        )
    guess = next((p for dep, p in DEFAULT_PORTS.items() if dep in deps), 3000)
    pinned = re.search(r"(?:--port|-p)[=\s]+(\d{2,5})", scripts[script])
    if pinned:
        guess = int(pinned[1])
    return ServerSpec(
        f"{tool} run {script}",
        project,
        [tool, "run", script],
        guess_url=f"http://localhost:{guess}/",
        # Create React App and friends open a browser tab unless told not to.
        env={"BROWSER": "none", "NO_COLOR": "1", "FORCE_COLOR": "0"},
        setup=setup,
    )


def _python_app(project: Path, port: int) -> ServerSpec | None:
    candidates = [
        p
        for folder in (project, project / "app", project / "src", *project.glob("src/*"))
        if folder.is_dir()
        for p in sorted(folder.glob("*.py"))
    ]
    for path in candidates[:60]:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = re.search(r"^(\w+)\s*(?::[^=]+)?=\s*(FastAPI|Flask)\(", source, re.MULTILINE)
        factory = re.search(r"^def create_app\(", source, re.MULTILINE)
        if not found and not (factory and "Flask" in source):
            continue

        relative = path.relative_to(project).with_suffix("")
        cwd = project
        if relative.parts[0] == "src":
            cwd, relative = project / "src", relative.relative_to("src")
        module = ".".join(relative.parts)
        fastapi = bool(found and found[2] == "FastAPI")
        python, setup = _python(project, ["fastapi", "uvicorn"] if fastapi else ["flask"])
        if fastapi:
            command = [python, "-m", "uvicorn", f"{module}:{found[1]}"]
            command += ["--host", "127.0.0.1", "--port", str(port)]
            label = "FastAPI (uvicorn)"
        else:
            app = f"{module}:{found[1]}" if found else module
            command = [python, "-m", "flask", "--app", app, "run"]
            command += ["--host", "127.0.0.1", "--port", str(port)]
            label = "Flask"
        return ServerSpec(label, cwd, command, url=f"http://127.0.0.1:{port}/", setup=setup)
    return None


def _dotnet(project: Path, port: int) -> ServerSpec | None:
    for csproj in sorted([*project.glob("*.csproj"), *project.glob("*/*.csproj")]):
        if "test" in csproj.stem.lower():
            continue
        text = csproj.read_text(encoding="utf-8", errors="replace")
        if 'Sdk="Microsoft.NET.Sdk.Web"' not in text:
            continue
        if not shutil.which("dotnet"):
            raise ProjectError(_NEEDS["dotnet"])
        url = f"http://127.0.0.1:{port}"
        return ServerSpec(
            f"ASP.NET Core ({csproj.name})",
            csproj.parent,
            # The launch profile would override the address we pick.
            ["dotnet", "run", "--project", csproj.name, "--no-launch-profile"],
            url=url + "/",
            env={"ASPNETCORE_URLS": url, "ASPNETCORE_ENVIRONMENT": "Development"},
            timeout=180,
        )
    return None


_NEEDS = {
    "npm": "This project needs Node.js, which isn't installed. "
    "Get the LTS version from https://nodejs.org and try again.",
    "pnpm": "This project uses pnpm, which isn't installed. "
    "With Node.js installed, run: npm install -g pnpm",
    "yarn": "This project uses Yarn, which isn't installed. "
    "With Node.js installed, run: npm install -g yarn",
    "bun": "This project uses Bun, which isn't installed. See https://bun.sh",
    "dotnet": "This project needs the .NET SDK, which isn't installed. "
    "Get it from https://dotnet.microsoft.com/download and try again.",
}


def run_setup(step: SetupStep, on_line=None) -> None:
    """Run one setup step, passing its output lines along. Raises ProjectError if it fails."""
    try:
        process = subprocess.Popen(
            step.shell_command(),
            shell=True,
            cwd=step.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0"},
        )
    except OSError as exc:
        raise ProjectError(f"Couldn't run {step.shell_command()}: {exc}") from exc
    tail: deque[str] = deque(maxlen=30)
    for line in process.stdout:
        line = ANSI.sub("", line).rstrip()
        if line.strip():
            tail.append(line)
            if on_line:
                on_line(line)
    if process.wait() != 0:
        raise ProjectError(
            f"{step.description} failed (exit code {process.returncode})." + _last_lines(tail)
        )


def _static(project: Path, port: int) -> ServerSpec | None:
    if not (project / "index.html").is_file():
        return None
    return ServerSpec("static files", project, None)


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@contextlib.contextmanager
def _static_server(root: Path) -> Iterator[str]:
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


def _answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except urllib.error.HTTPError:
        # A 404 or 500 still means the server is up.
        return True
    except (urllib.error.URLError, OSError):
        return False


def _from_output(line: str) -> str | None:
    line = ANSI.sub("", line)
    match = PRINTED_URL.search(line)
    if match:
        url = re.sub(r"//(0\.0\.0\.0|\[::1?\])", "//localhost", match[0])
        return url if url.endswith("/") else url + "/"
    match = PRINTED_PORT.search(line)
    return f"http://localhost:{match[1]}/" if match else None


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    # Dev servers start children of their own (node, the reloader), so stop the whole tree.
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)


@contextlib.contextmanager
def serve(spec: ServerSpec) -> Iterator[str]:
    """Start the server, wait until it answers, yield its address and stop it afterwards."""
    if spec.command is None:
        with _static_server(spec.cwd) as url:
            yield url
        return

    extra = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        extra["start_new_session"] = True
    try:
        process = subprocess.Popen(
            spec.shell_command(),
            shell=True,
            cwd=spec.cwd,
            env={**os.environ, **spec.env},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **extra,
        )
    except OSError as exc:
        raise ProjectError(f"Couldn't start {spec.label}: {exc}") from exc

    tail: deque[str] = deque(maxlen=30)
    printed: list[str] = []

    def read() -> None:
        for line in process.stdout:
            tail.append(line.rstrip())
            if not printed and (url := _from_output(line)):
                printed.append(url)

    threading.Thread(target=read, daemon=True).start()
    try:
        url = _wait(process, spec, printed, tail)
        yield url
    finally:
        _stop(process)


def _wait(process: subprocess.Popen, spec: ServerSpec, printed: list[str], tail: deque) -> str:
    started = time.monotonic()
    while True:
        elapsed = time.monotonic() - started
        if process.poll() is not None:
            raise ProjectError(
                f"{spec.label} stopped before it was ready (exit code {process.returncode})."
                + _last_lines(tail)
            )
        url = spec.url or (printed[0] if printed else None)
        if not url and elapsed > GUESS_AFTER_S:
            url = spec.guess_url
        if url and _answers(url):
            return url
        if elapsed > spec.timeout:
            hint = "" if url else " It didn't print an address; pass --base-url to say where it is."
            raise ProjectError(
                f"{spec.label} didn't answer within {spec.timeout:.0f}s.{hint}" + _last_lines(tail)
            )
        time.sleep(0.3)


def _last_lines(tail: deque, count: int = 12) -> str:
    lines = [line for line in list(tail)[-count:] if line.strip()]
    return ("\nLast output:\n  " + "\n  ".join(lines)) if lines else ""
