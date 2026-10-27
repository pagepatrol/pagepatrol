from typing import Any

from playwright.async_api import Browser, Playwright
from playwright.async_api import Error as PlaywrightError

from pagepatrol.models import Viewport

BROWSERS = ("chromium", "firefox", "webkit")
DEFAULT_BROWSER = "chromium"
# What people call them, for messages and reports.
NAMES = {"chromium": "Chromium", "firefox": "Firefox", "webkit": "WebKit (Safari)"}


class BrowserNotInstalled(RuntimeError):
    pass


def parse_browsers(values: list[str]) -> list[str]:
    """Accept names like "firefox", "safari" or "all" and return Playwright's names in order."""
    aliases = {"chrome": "chromium", "edge": "chromium", "safari": "webkit"}
    picked: list[str] = []
    for value in values:
        for part in value.lower().replace(",", " ").split():
            names = BROWSERS if part == "all" else [aliases.get(part, part)]
            for name in names:
                if name not in BROWSERS:
                    raise ValueError(
                        f"Unknown browser {part!r}. Use chromium, firefox, webkit (Safari) or all."
                    )
                if name not in picked:
                    picked.append(name)
    return picked or [DEFAULT_BROWSER]


async def launch(playwright: Playwright, name: str, headless: bool = True) -> Browser:
    try:
        return await getattr(playwright, name).launch(headless=headless)
    except PlaywrightError as exc:
        if "Executable doesn't exist" in str(exc):
            hint = "" if name == DEFAULT_BROWSER else f" --browser {name}"
            raise BrowserNotInstalled(
                f"{NAMES[name]} isn't installed yet. Run: pagepatrol install{hint}"
            ) from exc
        raise


def context_options(name: str, viewport: Viewport) -> dict[str, Any]:
    mobile = viewport.width < 1024
    options: dict[str, Any] = {
        "viewport": {"width": viewport.width, "height": viewport.height},
        "has_touch": mobile,
    }
    # Firefox can't emulate a phone's meta viewport handling; it renders at
    # the given width, which is still what we measure.
    if name != "firefox":
        options["is_mobile"] = mobile
    return options
