import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pagepatrol import cli
from pagepatrol.config import LLMConfig
from pagepatrol.llm import Usage
from pagepatrol.planner import (
    build_request,
    collect_sources,
    parse_plan,
    redact,
    write_scenarios,
)
from pagepatrol.scenario import load_scenarios

FAKE_KEY = "sk-test-not-a-real-key-000000"

APP_PY = f"""
from flask import Flask, render_template, request

app = Flask(__name__)
API_KEY = "{FAKE_KEY}"
app.config["SECRET_KEY"] = "dev-secret-value-123"

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        return render_template("contact.html", sent=True)
    return render_template("contact.html")
"""

CONTACT_HTML = """
<h1>Contact us</h1>
{% if sent %}<p>Thanks for your message</p>{% endif %}
<form method="post">
  <label for="email">Email</label><input id="email" name="email">
  <button>Send</button>
</form>
"""


def make_project(root: Path) -> Path:
    files = {
        "app.py": APP_PY,
        "templates/contact.html": CONTACT_HTML,
        "templates/index.html": "<h1>Home</h1><a href='/contact'>Contact us</a>",
        ".env": "DATABASE_PASSWORD=super-secret-db-password\n",
        "config/credentials.py": "TOKEN = 'abc'\n",
        "node_modules/lib/index.js": "module.exports = 1;\n",
        "static/app.min.js": "var a=1;\n",
        "tests/test_app.py": "def test_home(): pass\n",
        "README.md": "# Demo\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_collects_source_and_leaves_secrets_out(tmp_path):
    sources = collect_sources(make_project(tmp_path))
    names = [rel for rel, _ in sources.files]

    assert sorted(names) == ["app.py", "templates/contact.html", "templates/index.html"]
    assert sources.secret_files == 2  # .env and credentials.py
    text = build_request(sources, []).text
    assert FAKE_KEY not in text
    assert "dev-secret-value-123" not in text
    assert "super-secret-db-password" not in text
    assert 'API_KEY = "<redacted>"' in text
    assert "Thanks for your message" in text


def test_gitignored_files_are_not_sent(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    make_project(tmp_path)
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "notes.html").write_text("<p>internal</p>", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("private/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    names = [rel for rel, _ in collect_sources(tmp_path).files]
    assert "private/notes.html" not in names
    assert "app.py" in names


def test_files_that_dont_fit_are_counted(tmp_path):
    sources = collect_sources(make_project(tmp_path), max_chars=500)
    assert sources.left_out >= 1
    assert sources.chars <= 500


PLAN = {
    "scenarios": [
        {
            "name": "Contact form",
            "why": "Visitors reach the shop this way.",
            "url": "/contact",
            "needs_login": False,
            "steps": [
                {"kind": "do", "text": "Fill in Email with test@example.com and press Send"},
                {"kind": "expect", "text": "Thanks for your message"},
                {"kind": "expect_missing", "text": "Error"},
            ],
        },
        {
            "name": "Account page",
            "why": "",
            "url": "/account",
            "needs_login": True,
            "steps": [{"kind": "goto", "text": "/account"}, {"kind": "expect", "text": "Account"}],
        },
        {"name": "Empty", "why": "", "url": "/", "needs_login": False, "steps": []},
    ],
    "notes": "No login form found.",
}


def test_plan_becomes_runnable_scenario_files(tmp_path):
    plan = parse_plan(PLAN)
    assert [s.name for s in plan.scenarios] == ["Contact form", "Account page"]
    assert plan.dropped and "Empty" in plan.dropped[0]

    paths = write_scenarios(plan.scenarios, tmp_path)
    loaded = load_scenarios(tmp_path)
    contact = next(s for s in loaded if s.name == "Contact form")
    assert [s.action for s in contact.steps] == ["do", "expect", "expect"]
    assert contact.steps[2].value == {"no_text": "Error"}
    assert contact.url == "/contact"

    account = (tmp_path / "account-page.yaml").read_text(encoding="utf-8")
    assert "Needs a signed-in user" in account

    # A second plan never overwrites what's there.
    again = write_scenarios(plan.scenarios[:1], tmp_path)
    assert again[0].name == "contact-form-2.yaml"
    assert paths[0].read_text(encoding="utf-8") == again[0].read_text(encoding="utf-8")


class FakePlanner:
    def __init__(self):
        self.model = "fake-model"
        self.usage = Usage()
        self.requests = []

    async def complete_json(self, request):
        self.requests.append(request)
        self.usage.add(5000, 500)
        return PLAN


def test_plan_command_saves_only_what_you_pick(tmp_path, monkeypatch):
    project = make_project(tmp_path / "project")
    out = tmp_path / "scenarios"
    fake = FakePlanner()
    monkeypatch.setattr(
        cli, "_llm_client", lambda **_: (fake, LLMConfig("anthropic", "claude-sonnet-5-5"))
    )

    result = CliRunner().invoke(cli.app, ["plan", str(project), "-o", str(out)], input="y\n1\n")

    assert result.exit_code == 0, result.output
    assert "Will send 3 file(s)" in result.output
    assert "Skipped 2 file(s) that may hold secrets" in result.output
    assert [p.name for p in out.iterdir()] == ["contact-form.yaml"]
    assert FAKE_KEY not in fake.requests[0].text


def test_plan_command_stops_if_you_say_no(tmp_path, monkeypatch):
    fake = FakePlanner()
    monkeypatch.setattr(cli, "_llm_client", lambda **_: (fake, LLMConfig("ollama", "llama")))
    result = CliRunner().invoke(
        cli.app, ["plan", str(make_project(tmp_path)), "-o", str(tmp_path / "s")], input="n\n"
    )
    assert result.exit_code == 1
    assert fake.requests == []


@pytest.mark.parametrize(
    ("line", "hidden"),
    [
        ('const token = "abc123def456";', True),
        ('"password": "hunter2hunter2"', True),
        ("STRIPE_SECRET_KEY='sk_live_abcdef'", True),
        ('<input type="password" name="password">', False),
        ('label = "Confirm password"', False),
        ("if password == other_password:", False),
    ],
)
def test_masking(line, hidden):
    masked, count = redact(line)
    assert (count > 0) is hidden, masked
    if not hidden:
        assert masked == line
