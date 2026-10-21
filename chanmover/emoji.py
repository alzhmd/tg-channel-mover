"""Ordinary emoji -> Telegram Premium (custom) emoji.

Only a Premium *user* account can put custom emoji into channel posts (bots cannot, whoever owns them), so this is used
by the Premium-account engine: it finds ordinary emoji in a post and returns the custom-emoji entities to add.
The map file is {"ids": {"😀": "<custom emoji id>"}, "aliases": {"🎥": "📹"}}; aliases name a close emoji that does have
a premium twin and are only used when explicitly enabled (they change the visible character).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

VS = "️"
MAX_CUSTOM_EMOJI = 100  # Telegram renders at most 100 custom emoji per message
DEFAULT_MAP = Path(__file__).parent / "data" / "premium_emoji.json"
Span = tuple[int, int, str]  # (offset, length) in UTF-16 code units, custom emoji id


def u16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _norm(emoji: str) -> str:
    return emoji.replace(VS, "")


class EmojiMap:
    def __init__(self, ids: dict[str, str], aliases: dict[str, str] | None = None, use_aliases: bool = False):
        self.ids = {_norm(k): v for k, v in ids.items()}
        self.aliases = {_norm(k): _norm(v) for k, v in (aliases or {}).items()} if use_aliases else {}
        keys = sorted(set(self.ids) | set(self.aliases), key=len, reverse=True)
        # tolerate a variation selector after every code point, and never match a prefix of a longer ZWJ / skin-tone sequence
        pattern = "|".join((VS + "?").join(re.escape(ch) for ch in key) for key in keys)
        self._rx = re.compile(f"(?:{pattern}){VS}?(?![‍\U0001F3FB-\U0001F3FF])") if keys else None

    @classmethod
    def load(cls, path: str | Path | None = None, use_aliases: bool = False) -> EmojiMap:
        data = json.loads(Path(path or DEFAULT_MAP).read_text(encoding="utf-8"))
        return cls(data["ids"], data.get("aliases"), use_aliases)

    def resolve(self, emoji: str) -> tuple[str, str] | None:
        """(emoji to show, custom emoji id) or None when there is no premium twin."""
        e = _norm(emoji)
        e = self.aliases.get(e, e)
        cid = self.ids.get(e)
        return (e, cid) if cid else None

    def upgrade(self, text: str, existing: list[tuple[int, int, str]] | None = None) -> tuple[str, list[Span]]:
        """Return (text, custom-emoji spans to add). `existing` are (offset, length, kind) of the post's own entities:
        emoji already custom, or inside code/pre, are left alone. With aliases enabled the text may change a character."""
        if not text or not self._rx:
            return text, []
        existing = existing or []
        blocked = [(o, o + n) for o, n, kind in existing if kind in ("custom_emoji", "code", "pre")]
        budget = MAX_CUSTOM_EMOJI - sum(1 for _, _, kind in existing if kind == "custom_emoji")
        out: list[Span] = []
        pieces: list[str] = []
        last = 0
        shift = 0  # UTF-16 shift caused by alias substitutions of a different width (rare)
        for m in self._rx.finditer(text):
            if budget <= 0:
                break
            start = u16(text[:m.start()])
            end = start + u16(m.group(0))
            if any(s < end and start < e for s, e in blocked):
                continue
            hit = self.resolve(m.group(0))
            if not hit:
                continue
            shown, cid = hit
            if shown == _norm(m.group(0)):  # same emoji: keep the author's exact characters (variation selector...)
                shown = m.group(0)
            pieces.append(text[last:m.start()])
            pieces.append(shown)
            last = m.end()
            width = u16(shown)
            out.append((start + shift, width, cid))
            shift += width - (end - start)
            budget -= 1
        pieces.append(text[last:])
        return "".join(pieces), out


@lru_cache(maxsize=4)
def load_map(path: str | None = None, use_aliases: bool = False) -> EmojiMap:
    return EmojiMap.load(path, use_aliases)
