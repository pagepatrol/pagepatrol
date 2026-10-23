import re
from pathlib import Path

from pagepatrol.models import Viewport

VIEWPORTS = {
    "mobile": Viewport(name="mobile", width=375, height=812),
    "tablet": Viewport(name="tablet", width=768, height=1024),
    "desktop": Viewport(name="desktop", width=1280, height=800),
}

_SIZE = re.compile(r"^(\d{2,5})x(\d{2,5})$")


class TargetError(ValueError):
    pass


def resolve_target(target: str) -> str:
    """Turn a URL, host:port, HTML file or folder into something the browser can open."""
    if re.match(r"^https?://", target, re.IGNORECASE) or target.startswith("file://"):
        return target

    path = Path(target)
    if path.is_file():
        return path.resolve().as_uri()
    if path.is_dir():
        index = path / "index.html"
        if index.is_file():
            return index.resolve().as_uri()
        raise TargetError(f"No index.html in {target}. Point to an HTML file or a URL instead.")

    if path.suffix.lower() in {".html", ".htm"}:
        raise TargetError(f"File not found: {target}")

    if re.match(r"^(localhost|127\.0\.0\.1|[\w.-]+\.\w{2,})(:\d+)?(/.*)?$", target):
        scheme = "http" if target.startswith(("localhost", "127.0.0.1")) else "https"
        return f"{scheme}://{target}"

    raise TargetError(f"Can't find {target!r}. Expected a URL, an HTML file or a folder.")


def parse_viewport(value: str) -> Viewport:
    value = value.strip().lower()
    if value in VIEWPORTS:
        return VIEWPORTS[value]
    match = _SIZE.match(value)
    if match:
        width, height = int(match[1]), int(match[2])
        return Viewport(name=value, width=width, height=height)
    names = ", ".join(VIEWPORTS)
    raise TargetError(f"Unknown viewport {value!r}. Use one of {names} or WIDTHxHEIGHT.")
