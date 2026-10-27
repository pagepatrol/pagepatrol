import json
import shutil
import sys
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pagepatrol.cli import app
from pagepatrol.project import (
    ProjectError,
    ServerSpec,
    _from_output,
    detect,
    is_project,
    load_config,
    save_config,
    serve,
)

SITE = Path(__file__).parent / "fixtures" / "site"


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def npm_project(tmp_path: Path, scripts: dict, deps: dict | None = None) -> Path:
    write(
        tmp_path / "package.json", json.dumps({"scripts": scripts, "devDependencies": deps or {}})
    )
    return tmp_path


def test_vite_project(tmp_path):
    npm_project(tmp_path, {"dev": "vite", "build": "vite build"}, {"vite": "^8"})
    (tmp_path / "node_modules").mkdir()
    spec = detect(tmp_path)
    assert spec.command == ["npm", "run", "dev"]
    assert spec.url is None
    assert spec.guess_url == "http://localhost:5173/"
    assert spec.env["BROWSER"] == "none"


def test_npm_lockfile_and_pinned_port(tmp_path):
    npm_project(tmp_path, {"start": "next dev -p 4000"}, {"next": "15"})
    (tmp_path / "node_modules").mkdir()
    write(tmp_path / "pnpm-lock.yaml")
    spec = detect(tmp_path)
    assert spec.command == ["pnpm", "run", "start"]
    assert spec.guess_url == "http://localhost:4000/"


def test_npm_needs_installed_dependencies(tmp_path):
    npm_project(tmp_path, {"dev": "vite"}, {"vite": "^8"})
    with pytest.raises(ProjectError, match="npm install"):
        detect(tmp_path)


def test_npm_without_dependencies(tmp_path):
    npm_project(tmp_path, {"dev": "node server.js"})
    assert detect(tmp_path).command == ["npm", "run", "dev"]


def test_django(tmp_path):
    write(tmp_path / "site" / "manage.py")
    spec = detect(tmp_path)
    assert spec.label == "Django"
    assert spec.cwd == (tmp_path / "site").resolve()
    assert spec.command[1:3] == ["manage.py", "runserver"]
    assert spec.url.startswith("http://127.0.0.1:")


def test_fastapi_uses_project_venv(tmp_path):
    write(tmp_path / "app" / "main.py", "from fastapi import FastAPI\n\napi = FastAPI()\n")
    python = write(tmp_path / ".venv" / "Scripts" / "python.exe")
    spec = detect(tmp_path)
    assert spec.command[0] == str(python.resolve())
    assert spec.command[1:4] == ["-m", "uvicorn", "app.main:api"]
    assert spec.cwd == tmp_path.resolve()


def test_flask_factory_in_src(tmp_path):
    source = "from flask import Flask\n\ndef create_app():\n    return Flask(__name__)\n"
    write(tmp_path / "src" / "shop" / "web.py", source)
    spec = detect(tmp_path)
    assert spec.label == "Flask"
    assert spec.command[1:5] == ["-m", "flask", "--app", "shop.web"]
    assert spec.cwd == (tmp_path / "src").resolve()


def test_dotnet_skips_test_projects(tmp_path):
    write(tmp_path / "Shop.Tests" / "Shop.Tests.csproj", '<Project Sdk="Microsoft.NET.Sdk.Web">')
    write(tmp_path / "Shop" / "Shop.csproj", '<Project Sdk="Microsoft.NET.Sdk.Web">')
    spec = detect(tmp_path)
    assert spec.command[:4] == ["dotnet", "run", "--project", "Shop.csproj"]
    assert spec.cwd == (tmp_path / "Shop").resolve()
    assert spec.env["ASPNETCORE_URLS"] + "/" == spec.url


def test_static_folder():
    spec = detect(SITE)
    assert spec.command is None
    assert is_project(SITE)


def test_unknown_project(tmp_path):
    write(tmp_path / "requirements.txt", "requests\n")
    assert is_project(tmp_path)
    with pytest.raises(ProjectError, match="--start"):
        detect(tmp_path)


def test_config_wins_over_detection(tmp_path):
    shutil.copytree(SITE, tmp_path, dirs_exist_ok=True)
    save_config(tmp_path, 'npm run "dev"', "http://localhost:5000")
    assert load_config(tmp_path) == {"command": 'npm run "dev"', "url": "http://localhost:5000"}
    spec = detect(tmp_path)
    assert spec.command == 'npm run "dev"'
    assert spec.url == "http://localhost:5000"


@pytest.mark.parametrize(
    "line, url",
    [
        (
            "  \x1b[32m➜\x1b[39m  \x1b[1mLocal\x1b[22m:   \x1b[36mhttp://localhost:\x1b[1m5173\x1b[22m/\x1b[39m",
            "http://localhost:5173/",
        ),
        ("   - Local:        http://localhost:3001", "http://localhost:3001/"),
        ("Serving HTTP on 0.0.0.0 port 8000 (http://0.0.0.0:8000/) ...", "http://localhost:8000/"),
        ("Server listening on port 8080", "http://localhost:8080/"),
        ("See https://nextjs.org/telemetry", None),
    ],
)
def test_address_from_output(line, url):
    assert _from_output(line) == url


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read().decode()


def gone(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=2)
    except OSError:
        return True
    return False


def test_serves_static_files():
    with serve(detect(SITE)) as url:
        assert "Show details" in fetch(url)
        assert "About" in fetch(url + "about.html")
    assert gone(url)


def test_starts_and_stops_a_command():
    # Port 0 makes http.server pick a free port and print it, like dev servers do.
    command = [sys.executable, "-u", "-m", "http.server", "0", "--bind", "127.0.0.1"]
    spec = ServerSpec("http.server", SITE, command)
    with serve(spec) as url:
        assert "Show details" in fetch(url)
    assert gone(url)


def test_reports_a_server_that_dies():
    command = [sys.executable, "-c", "print('boom: missing module'); raise SystemExit(3)"]
    with (
        pytest.raises(ProjectError, match=r"exit code 3(.|\n)*boom: missing module"),
        serve(ServerSpec("broken", SITE, command)),
    ):
        pass


def scenario_project(tmp_path: Path) -> Path:
    project = tmp_path / "site"
    shutil.copytree(SITE, project)
    write(
        project / ".pagepatrol" / "scenarios" / "about.yaml",
        "name: About opens\nurl: /index.html\nsteps:\n  - click: About\n  - expect: About\n",
    )
    return project


def test_run_project_folder(tmp_path):
    project = scenario_project(tmp_path)
    result = CliRunner().invoke(app, ["run", str(project), "--fail-on", "never"])
    assert result.exit_code == 0, result.output
    assert "Starting static files" in result.output
    assert "PASS" in result.output
    assert list((project / ".pagepatrol" / "runs").glob("*/report.html"))


def test_run_with_start_command_remembers_it(tmp_path):
    project = scenario_project(tmp_path)
    start = f'"{sys.executable}" -u -m http.server 0 --bind 127.0.0.1'
    result = CliRunner().invoke(app, ["run", str(project), "--start", start, "--fail-on", "never"])
    assert result.exit_code == 0, result.output
    assert load_config(project)["command"] == start


def test_run_project_without_scenarios(tmp_path):
    project = tmp_path / "site"
    shutil.copytree(SITE, project)
    result = CliRunner().invoke(app, ["run", str(project)])
    assert result.exit_code == 2
    assert "pagepatrol plan" in result.output
