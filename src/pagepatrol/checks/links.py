import asyncio
from pathlib import Path
from urllib.parse import urldefrag, urlparse
from urllib.request import url2pathname

from playwright.async_api import APIRequestContext
from playwright.async_api import Error as PlaywrightError

from pagepatrol.checks.layout import PageLink
from pagepatrol.models import Finding, Severity

MAX_LINKS = 100
CONCURRENCY = 4
BROKEN = {404, 410}
# 419 isn't standard, but Hacker News answers fast link checks with it the
# way most sites use 429.
RATE_LIMITED = {419, 429}


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


async def _status(request: APIRequestContext, url: str) -> int:
    response = await request.head(url, timeout=10_000)
    # Plenty of servers don't implement HEAD properly.
    if response.status in (403, 405, 501):
        response = await request.get(url, timeout=10_000)
    return response.status


async def check_links(
    links: list[PageLink],
    page_url: str,
    request: APIRequestContext,
    include_external: bool = False,
) -> list[Finding]:
    page_base = urldefrag(page_url).url
    seen: dict[str, PageLink] = {}
    for link in links:
        url = urldefrag(link.href).url
        scheme = urlparse(url).scheme
        if url == page_base or url in seen:
            continue
        if scheme == "file" or (
            scheme in ("http", "https") and (include_external or _same_origin(url, page_url))
        ):
            seen[url] = link
        if len(seen) >= MAX_LINKS:
            break

    semaphore = asyncio.Semaphore(CONCURRENCY)
    blocked_by: list[int] = []
    skipped = 0

    async def check_one(url: str, link: PageLink) -> Finding | None:
        nonlocal skipped
        if url.startswith("file:"):
            path = Path(url2pathname(urlparse(url).path))
            if path.exists():
                return None
            return _broken(link, url, "Link points to a file that doesn't exist")

        async with semaphore:
            if blocked_by:
                skipped += 1
                return None
            try:
                status = await _status(request, url)
            except PlaywrightError as exc:
                finding = _broken(link, url, "Link could not be reached")
                finding.severity = Severity.WARNING
                finding.details["error"] = str(exc).splitlines()[0]
                return finding

        if status in RATE_LIMITED:
            blocked_by.append(status)
            skipped += 1
            return None
        if status in BROKEN or status >= 500:
            finding = _broken(link, url, f"Link returns HTTP {status}")
        elif status >= 400:
            # 401/403 and friends usually mean "log in first" or "no bots",
            # not a dead link.
            finding = _broken(link, url, f"Link returns HTTP {status}, couldn't verify it")
            finding.severity = Severity.INFO
        else:
            return None
        finding.details["status"] = status
        return finding

    results = await asyncio.gather(*(check_one(url, link) for url, link in seen.items()))
    findings = [f for f in results if f]
    if blocked_by:
        findings.append(
            Finding(
                check="broken-link",
                category="content",
                severity=Severity.INFO,
                message=f"Stopped checking links after the server answered HTTP {blocked_by[0]} "
                f"(rate limited); {skipped} links weren't checked",
            )
        )
    return findings


def _broken(link: PageLink, url: str, message: str) -> Finding:
    return Finding(
        check="broken-link",
        category="content",
        severity=Severity.ERROR,
        message=message,
        selector=link.selector,
        rect=link.rect,
        details={"href": url},
    )
