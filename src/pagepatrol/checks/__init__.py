from functools import cache
from importlib.resources import files

from playwright.async_api import Page


@cache
def load_js(name: str) -> str:
    return files("pagepatrol.checks").joinpath(name).read_text(encoding="utf-8")


async def install_helpers(page: Page) -> None:
    await page.evaluate(load_js("js/helpers.js"))
