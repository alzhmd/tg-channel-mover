"""Re-point links to posts of the old channel at the matching posts of the new one.

A copied post still carries the links it had: "https://t.me/OldChannel/4098" on a film name sends the reader to the old
channel (which may be deleted). Given the old->new post ids of a transfer, this rewrites those links (hyperlinks and bare
URLs in the text) to the new channel and leaves every other entity (bold, custom emoji, ...) intact.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

LINK_RE = re.compile(
    r"(?P<scheme>https?://)?(?:t|telegram)\.me/(?P<chat>c/\d+|[A-Za-z][A-Za-z0-9_]{3,31})/(?P<id>\d+)(?P<tail>(?:[/?#][^\s]*)?)",
    re.I)


@dataclass
class Rule:
    """Links that point into one old channel, and where its posts went."""
    usernames: set[str]            # lowercase names of the old channel (current and former)
    channel_ids: set[int]          # numeric ids (the 123 in t.me/c/123/45)
    mapping: dict[int, int]        # old post id -> new post id

    def matches(self, chat: str) -> bool:
        chat = chat.lower()
        if chat.startswith("c/"):
            return int(chat[2:]) in self.channel_ids
        return chat in self.usernames


@dataclass
class Target:
    """The channel whose posts the new links point at."""
    username: str | None
    channel_id: int                # positive part of -100<id>

    def url(self, post_id: int, single: bool = False) -> str:
        base = f"https://t.me/{self.username}/{post_id}" if self.username else f"https://t.me/c/{self.channel_id}/{post_id}"
        return base + ("?single" if single else "")


@dataclass
class Report:
    rewritten: int = 0     # links changed
    unmapped: int = 0      # links into the old channel whose post was not copied (left as they were)
    unmapped_ids: list[int] = field(default_factory=list)


def new_url(url: str, rules: list[Rule], target: Target, report: Report) -> str | None:
    """The replacement for one URL, or None when it is not a link into an old channel (or its post has no copy)."""
    m = LINK_RE.fullmatch(url.strip())
    if not m:
        return None
    for rule in rules:
        if rule.matches(m.group("chat")):
            old = int(m.group("id"))
            new = rule.mapping.get(old)
            if new is None:
                report.unmapped += 1
                report.unmapped_ids.append(old)
                return None
            report.rewritten += 1
            return target.url(new, single="single" in (m.group("tail") or "").lower())
    return None


def make_rewriter(rules: list[Rule], target: Target):
    """(text, entities) -> (text, entities, number of links changed): the shape the copy engine plugs in."""
    def link_rewrite(text, entities):
        new_text, new_entities, report = rewrite(text, entities, rules, target)
        return new_text, new_entities, report.rewritten
    return link_rewrite


def _u16(s: str) -> bytes:
    return s.encode("utf-16-le")


def rewrite(text: str, entities, rules: list[Rule], target: Target) -> tuple[str, list, Report]:
    """(new text, new entities, report). `entities` are Telethon MessageEntity objects (TextUrl / Url are the ones changed)."""
    report = Report()
    raw = _u16(text or "")
    new_entities = []
    spans: list[tuple[int, int, bytes]] = []   # bare URLs replaced in the text: (start, end, new bytes), UTF-16 units * 2
    for e in entities or []:
        kind = type(e).__name__
        if kind == "MessageEntityTextUrl":
            url = new_url(e.url, rules, target, report)
            if url:
                e = copy.copy(e)
                e.url = url
        elif kind == "MessageEntityUrl":
            shown = raw[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le")
            url = new_url(shown, rules, target, report)
            if url:
                spans.append((e.offset * 2, (e.offset + e.length) * 2, _u16(url)))
        new_entities.append(e)
    if not report.rewritten:
        return text, list(entities or []), report
    if spans:
        spans.sort()
        out, last = bytearray(), 0
        for start, end, repl in spans:
            out += raw[last:start] + repl
            last = end
        out += raw[last:]

        def remap(pos: int, is_end: bool) -> int:   # pos in UTF-16 units
            shift = 0
            for start, end, repl in spans:
                s, e_, n = start // 2, end // 2, len(repl) // 2
                if pos >= e_:
                    shift += n - (e_ - s)
                elif pos > s:                        # inside a replaced URL: snap to its edge
                    return s + shift + (n if is_end else 0)
            return pos + shift
        fixed = []
        for e in new_entities:
            e = copy.copy(e)
            start, end = e.offset, e.offset + e.length
            e.offset = remap(start, False)
            e.length = remap(end, True) - e.offset
            fixed.append(e)
        new_entities, raw = fixed, bytes(out)
    return raw.decode("utf-16-le"), new_entities, report
