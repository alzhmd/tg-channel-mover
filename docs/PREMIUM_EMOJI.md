# Premium emoji map

`chanmover/data/premium_emoji.json` maps ordinary emoji to Premium (custom) emoji ids:

```json
{ "ids": { "😀": "5372954454653933911" }, "aliases": { "🎥": "📹" } }
```

* `ids` - 1,054 emoji that have a Premium twin. Matching tolerates variation selectors, keycaps and never splits a ZWJ / skin-tone sequence.
* `aliases` - a close emoji for those without a twin (🎥 has none, 📹 does). They change the visible character, so they are used only when
  *Nearest substitute* is switched on.

Posts that already contain custom emoji keep them; emoji inside `code`/`pre` entities are left alone; at most 100 custom emoji are placed
in one message (Telegram's limit).

## Using your own map

Set `PREMIUM_EMOJI_FILE=/path/to/map.json`. Custom emoji ids can be read from any post that already uses them
(`MessageEntityCustomEmoji.document_id`), or from a sticker set with `getCustomEmojiStickers`.
