from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page

CLICK_ROLES = ["button", "link", "menuitem", "tab", "checkbox", "radio", "switch", "option"]
INPUT_ROLES = ["textbox", "searchbox", "combobox", "spinbutton"]
SELECT_ROLES = ["combobox", "listbox"]
MAX_VISIBLE_CHECKS = 10


class TargetNotFound(LookupError):
    pass


def from_spec(page: Page, spec: dict[str, Any]) -> Locator:
    exact = spec.get("exact", False)
    if "role" in spec:
        locator = page.get_by_role(spec["role"], name=spec.get("name"), exact=exact)
    elif "label" in spec:
        locator = page.get_by_label(spec["label"], exact=exact)
    elif "placeholder" in spec:
        locator = page.get_by_placeholder(spec["placeholder"], exact=exact)
    elif "text" in spec:
        locator = page.get_by_text(spec["text"], exact=exact)
    elif "testid" in spec:
        locator = page.get_by_test_id(spec["testid"])
    elif "css" in spec:
        locator = page.locator(spec["css"])
    else:
        raise TargetNotFound(f"Don't know how to find {spec}")
    return locator.nth(spec["nth"]) if "nth" in spec else locator


async def _first_visible(locator: Locator) -> int | None:
    count = await locator.count()
    for i in range(min(count, MAX_VISIBLE_CHECKS)):
        if await locator.nth(i).is_visible():
            return i
    return None


def _candidates(page: Page, text: str, action: str):
    if action in ("fill", "press"):
        roles = INPUT_ROLES
    elif action == "select":
        roles = SELECT_ROLES
    else:
        roles = CLICK_ROLES

    # Exact matches first, so "Send" doesn't pick "Send feedback" when both exist.
    for exact in (True, False):
        for role in roles:
            yield {"role": role, "name": text, "exact": exact}
        yield {"label": text, "exact": exact}
        yield {"placeholder": text, "exact": exact}
        if action not in ("fill", "select", "press"):
            yield {"text": text, "exact": exact}
    if action not in ("fill", "select", "press"):
        # Last resort for things like icon buttons with a title.
        yield {"css": f'[title="{text}" i], [aria-label="{text}" i]'}


async def resolve(page: Page, target: str | dict[str, Any], action: str) -> tuple[Locator, dict]:
    """Find the element a step refers to. Returns the locator and an exact spec
    that finds the same element again on replay."""
    if isinstance(target, dict):
        spec = dict(target)
        locator = from_spec(page, spec)
        if "nth" in spec:
            return locator, spec
        index = await _first_visible(locator)
        if index is None:
            raise TargetNotFound(f"Couldn't find a visible element for {target}")
        if await locator.count() > 1:
            spec["nth"] = index
        return locator.nth(index), spec

    for spec in _candidates(page, target, action):
        locator = from_spec(page, spec)
        index = await _first_visible(locator)
        if index is None:
            continue
        if await locator.count() > 1:
            spec["nth"] = index
        return locator.nth(index), spec

    raise TargetNotFound(f"Couldn't find {target!r} on the page")


async def accessible_name(locator: Locator) -> str:
    try:
        name = await locator.evaluate(
            "el => el.getAttribute('aria-label') || el.innerText || el.value || el.title || ''"
        )
    except PlaywrightError:
        return ""
    return " ".join(str(name).split())[:200]
