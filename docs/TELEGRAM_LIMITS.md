# What Telegram allows (measured)

Telegram does not publish most of these numbers. Everything below was **observed** on a real 14,000-post channel in October 2026
and may change; treat it as a guide, not a contract.

## Bots

| Fact | Evidence |
|---|---|
| A bot cannot read a channel's history, but `copyMessages` works on any id when the bot is a member | tested |
| `copyMessages` drops inline keyboards; `forwardMessage` does not return them either | tested (`reply_markup: null`) |
| **Bots cannot keep or send Premium (custom) emoji in channel posts**, even when the bot's *owner* has Premium | tested with a Premium-owned bot: private chat keeps the emoji, a channel strips them. Bot API 9.4 allows it only in private chats, groups and supergroups |
| Posting to one channel: roughly 60 posts/minute sustained with flood waits of 20-40 s | measured |
| `getChatMember` on a channel the bot is not in answers "member list is inaccessible" | observed |

## Premium user accounts

| Fact | Evidence |
|---|---|
| "Forward without author" (`drop_author`) keeps Premium emoji and hides the sender; 100 ids per call | tested |
| Re-sending a post with `formatting_entities` can add custom emoji (Premium accounts only) | tested |
| `FLOOD_WAIT` is **per account**, not per session: while a send wait runs, another session of the same account cannot send either | observed |
| Resuming at full speed right after a wait escalated it: 3-8 s waits → 1418 s → 1544 s | observed |
| Even at ~60 posts/minute a send (`SendMedia`) wait of ~25 minutes came every one or two hours | observed (hourly media quota) |
| **Editing**: only ~4-5 edits per minute; after ~20-25 edits a 4-minute wait follows | observed on a channel, 3 consecutive waits of 228-265 s |
| Reading history (`GetHistory`) at full speed is rate limited too (24-26 s waits); `wait_time=1.5-2 s` avoids it | observed |
| A few emoji of the bundled map are not accepted as custom emoji (keycaps such as 0️⃣, ⭐️, 📨 in our run): Telegram silently keeps the ordinary emoji. About 0.1 % of posts had one or two of them | observed (11 of 14,200 posts) |
| A `FLOOD_WAIT` is not a ban; the official docs only ask clients to wait the stated time | [core.telegram.org/api/errors](https://core.telegram.org/api/errors) |

## Channels and links

| Fact | Evidence |
|---|---|
| Message ids in a channel only grow; a new post gets the highest id ever used + 1, deleted ids are never reused | relied on for link prediction, verified on every window |
| `t.me/s/<name>` (web preview) shows an album once, under its first id | observed |
| Posts Telegram blocks for everyone (copyright) read "This message couldn't be displayed ... copyright infringement" and carry `restriction_reason`; they cannot be copied | observed |
| A former username can now belong to a different channel; links written with it still exist in old posts | observed |
| Links to a private channel have the form `t.me/c/<id>/<post>` and open only for its members | by design |
| Edited posts show an "edited" label | by design |
