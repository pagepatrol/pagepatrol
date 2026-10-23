from pathlib import Path

import pytest

from pagepatrol.targets import TargetError, parse_viewport, resolve_target

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("https://example.com/page", "https://example.com/page"),
        ("localhost:3000", "http://localhost:3000"),
        ("127.0.0.1:8000/app", "http://127.0.0.1:8000/app"),
        ("example.com", "https://example.com"),
    ],
)
def test_resolve_urls(target, expected):
    assert resolve_target(target) == expected


def test_resolve_file():
    url = resolve_target(str(FIXTURES / "clean.html"))
    assert url.startswith("file://") and url.endswith("/clean.html")


def test_resolve_folder_needs_index(tmp_path):
    with pytest.raises(TargetError):
        resolve_target(str(tmp_path))
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    assert resolve_target(str(tmp_path)).endswith("/index.html")


def test_missing_html_file_is_not_treated_as_a_domain():
    with pytest.raises(TargetError, match="File not found"):
        resolve_target("nope.html")


def test_parse_viewport():
    assert parse_viewport("mobile").width == 375
    custom = parse_viewport("1440x900")
    assert (custom.width, custom.height) == (1440, 900)
    with pytest.raises(TargetError):
        parse_viewport("huge")
