"""The API key must reach the provider and nothing else: not the terminal,
not the report, not the screenshots folder."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pagepatrol.cli import app
from pagepatrol.config import LLMConfig, save_key, save_llm_config

FIXTURES = Path(__file__).parent / "fixtures"
SECRET = "sk-test-DO-NOT-LEAK-7f3a9c"


class RejectingServer(BaseHTTPRequestHandler):
    """Answers every request with a 401, like a provider would for a bad key."""

    received_headers: list[str] = []

    def do_POST(self):
        self.received_headers.append(str(self.headers))
        body = json.dumps(
            {
                "type": "error",
                "error": {"type": "authentication_error", "message": "invalid api key"},
            }
        ).encode()
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    RejectingServer.received_headers = []
    httpd = HTTPServer(("127.0.0.1", 0), RejectingServer)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def all_output_bytes(folder: Path) -> bytes:
    return b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())


@pytest.mark.parametrize(
    ("provider", "suffix", "key_from"),
    [
        ("anthropic", "", "env"),
        ("anthropic", "", "keyring"),
        ("custom", "/v1", "env"),
    ],
)
def test_key_never_leaks(tmp_path, monkeypatch, server, provider, suffix, key_from):
    save_llm_config(LLMConfig(provider, "test-model", base_url=server + suffix))
    if key_from == "env":
        monkeypatch.setenv("PAGEPATROL_API_KEY", SECRET)
    else:
        save_key(provider, SECRET)

    out = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        app, ["check", str(FIXTURES / "overlap.html"), "-v", "desktop", "-o", str(out), "--llm"]
    )
    config_output = runner.invoke(app, ["config"]).output

    # The key did go where it should, so the test exercised the real path.
    assert any(SECRET in h for h in RejectingServer.received_headers)
    assert "AI review failed" in result.output

    assert SECRET not in result.output
    assert SECRET not in config_output
    assert SECRET.encode() not in all_output_bytes(out)
