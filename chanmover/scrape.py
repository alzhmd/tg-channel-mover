"""Lists the ids of every post of a *public* channel through its t.me/s/<username> web preview.

The Bot API cannot list a channel's history, so for public sources this gives the exact set of ids to copy (and the
newest id) without needing a user account. A private source falls back to scanning an id range instead.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import re
from typing import Awaitable, Callable

import httpx

POST_RE = re.compile(r'data-post="([^"/]+)/(\d+)"')
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; chanmover/1.0)", "Accept-Language": "en"}


def parse_ids(html: str, username: str) -> list[int]:
    """Post ids of `username` found on one preview page, ascending."""
    return sorted({int(i) for u, i in POST_RE.findall(html) if u.lower() == username.lower()})


TAG_RE = re.compile(r"<[^>]+>")
BTN_RE = re.compile(r'<a class="tgme_widget_message_inline_button( url_button)?" href="([^"]*)"[^>]*>(.*?)</a>', re.S)
Keyboard = list[list[tuple[str, str]]]  # rows of (text, url)


def parse_keyboards(html: str, username: str) -> dict[int, tuple[Keyboard, int]]:
    """Inline keyboards of the posts on one preview page: {post id: (URL-button rows, number of other buttons)}.

    Only URL buttons are visible (and meaningful) outside the original bot; callback buttons are counted as dropped."""
    found: dict[int, tuple[Keyboard, int]] = {}
    for chunk in html.split('<div class="tgme_widget_message_wrap')[1:]:
        m = POST_RE.search(chunk)
        start = chunk.find("tgme_widget_message_inline_keyboard")
        if not m or m.group(1).lower() != username.lower() or start < 0:
            continue
        rows: Keyboard = []
        dropped = 0
        for row_html in chunk[start:].split("tgme_widget_message_inline_row")[1:]:
            row = []
            for is_url, href, inner in BTN_RE.findall(row_html):
                text = html_lib.unescape(TAG_RE.sub("", inner)).strip()
                url = html_lib.unescape(href)
                if is_url and text and url.startswith(("http://", "https://", "tg://")):
                    row.append((text, url))
                else:
                    dropped += 1
            if row:
                rows.append(row)
        found[int(m.group(2))] = (rows, dropped)
    return found


async def list_public_ids(
    username: str,
    lo: int = 1,
    hi: int | None = None,
    delay: float = 0.35,
    progress: Callable[[int, int], Awaitable[None]] | None = None,
    client: httpx.AsyncClient | None = None,
    keyboards: dict[int, tuple[Keyboard, int]] | None = None,
) -> list[int]:
    """All post ids of a public channel within [lo, hi], ascending; [] when the channel has no web preview.

    `keyboards`, if given, is filled with the inline keyboards of the posts (see parse_keyboards)."""
    own = client is None
    client = client or httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True)
    found: set[int] = set()
    before: int | None = None if hi is None else hi + 1
    top = 0
    try:
        while True:
            url = f"https://t.me/s/{username}" + (f"?before={before}" if before else "")
            for attempt in range(6):
                try:
                    r = await client.get(url)
                    if r.status_code == 429:
                        await asyncio.sleep(int(r.headers.get("retry-after", "5")) + 1)
                        continue
                    r.raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt == 5:
                        raise
                    await asyncio.sleep(2 * (attempt + 1))
            page = parse_ids(r.text, username)
            if not page:
                break
            top = top or page[-1]
            if keyboards is not None:
                keyboards.update({i: kb for i, kb in parse_keyboards(r.text, username).items()
                                  if lo <= i and (hi is None or i <= hi)})
            found.update(i for i in page if lo <= i and (hi is None or i <= hi))
            if progress:
                await progress(len(found), page[0])
            if page[0] <= lo or (before is not None and page[0] >= before):
                break
            before = page[0]
            await asyncio.sleep(delay)
    finally:
        if own:
            await client.aclose()
    return sorted(found)
