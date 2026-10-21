<div align="center">

# Channel Mover

**Copy a whole Telegram channel into another one - no "Forwarded from", albums and captions intact, Premium emoji preserved, links re-pointed, resumable.**

[![CI](https://github.com/alzhmd/tg-channel-mover/actions/workflows/ci.yml/badge.svg)](https://github.com/alzhmd/tg-channel-mover/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
[![Code style: ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)

[فارسی](README.fa.md) · [Architecture](docs/ARCHITECTURE.md) · [Telegram limits](docs/TELEGRAM_LIMITS.md) · [Operations](docs/OPERATIONS.md) · [Changelog](CHANGELOG.md)

</div>

When a channel has to be abandoned or deleted, its audience and its content have to move. Forwarding 14,000 posts by hand shows the
original sender on every one, breaks albums and loses your Premium emoji. Channel Mover is a Telegram bot that does the whole job for
you, from a button menu, and keeps going after restarts and Telegram's flood limits.

It was built for - and verified on - a real 14,068-post film channel: two complete copies, 81 albums, 67,000 Premium emoji and 9,500 links
to old posts, every one checked against the source.

## Features

* **No sender, no "Forwarded from"** - posts arrive as if you had written them.
* **Everything is kept**: text, captions, formatting, files of any size, polls, stickers, albums, URL buttons and (with the Premium engine) **Premium emoji**.
* **Ordinary emoji become Premium emoji** automatically (optional).
* **Links to the old channel are re-pointed** at the matching posts of the new one - written correctly the first time, even for posts that do not exist yet.
* **Any range**: everything, *from post X to the end*, *up to post Y*, or *X to Y* - given as a link, a number or a forwarded post.
* **Resumable and safe**: pause / resume / cancel, survives restarts and crashes, never sends a post twice, waits out Telegram's flood limits.
* **Live mirror** of new posts of the source after the history is copied.
* **Exact old → new post ids** as CSV (to repair external links, indexes, bots).
* **Undo**: delete exactly the posts a transfer created.
* Reusable for any number of channels; private invite links accepted; owner-only access.

## Two engines

| | 🤖 Bot engine | 🌟 Premium-account engine |
|---|---|---|
| How | Bot API `copyMessages` | your Premium account (MTProto), linked by QR code |
| Premium emoji | **lost** - Telegram does not let bots place them in channels, not even Premium-owned bots | **kept**, and ordinary emoji are upgraded |
| Links to old posts | not rewritten | **rewritten while copying** |
| Speed | ~60 posts/min | ~60-75 posts/min by default (safe pace) |
| Needs | the bot as admin of both channels | `TG_API_ID`/`TG_API_HASH`; the account as member of the source and admin of the destination |
| Old → new ids | partly exact | always exact |

The bot picks the Premium engine by default when a Premium account is linked, and tells you what the other choice would cost.
Why the bot cannot do it: see [docs/TELEGRAM_LIMITS.md](docs/TELEGRAM_LIMITS.md).

## Quick start

1. **Create a bot** with [@BotFather](https://t.me/BotFather) and copy its token. Find your numeric Telegram id (for example with
   [@userinfobot](https://t.me/userinfobot)).
2. **Configure** - `cp .env.example .env` and set `BOT_TOKEN` and `ADMIN_IDS`. For the Premium engine also set `TG_API_ID` and `TG_API_HASH`
   (from [my.telegram.org](https://my.telegram.org) → *API development tools*).
3. **Run**
   ```bash
   docker compose up -d --build            # or:  make install && make run
   ```
   (systemd unit: `deploy/channel-mover.service`)
4. **Add the bot** as admin of the source (any right) and of the destination (*Post messages*). The channels appear in the bot by themselves.
5. **Premium engine only**: in the bot open *🌟 Premium account → 🔗 Connect with QR* and scan the code in
   *Telegram → Settings → Devices → Link Desktop Device*. Make sure that account is a member of the source and an admin of the destination.
6. Tap **➕ New transfer** → source → destination → options → **🚀 Continue** → **✅ Yes, start**. Watch the progress bar; pause or cancel any time.

> The bot's interface is currently **Persian**; translations are welcome (see [CONTRIBUTING](CONTRIBUTING.md)).

### Options of a transfer

| Option | Meaning |
|---|---|
| 🌟/🤖 Engine | Premium account or bot |
| 📍 From post / 🏁 To post | limit the range by link, number or forwarded post (both ends are copied) |
| 🔕 Silent | no notifications for the destination's subscribers (default on) |
| 🔄 Live mirror | keep copying new posts of the source |
| ✨ Premium emoji | turn ordinary emoji into Premium emoji (Premium engine) |
| 🔗 Links → this channel | re-point links to the old channel's posts; 🏷 *former usernames* of the old channel can be added |
| 🚫 Remove captions / 🔒 Protect content | as named |

## Pace, floods and your account

Telegram limits how fast an account may write and does not publish the numbers. A `FLOOD_WAIT` is a normal "slow down", not a ban - but
ignoring it makes the waits longer. Channel Mover therefore writes at a steady pace (`USER_MIN_INTERVAL`, 0.8 s per post), doubles it after
every wait, waits exactly the demanded time (also across restarts) and tells you about long waits. Expect a ~25-minute wait every hour or two on a big
channel (Telegram's hourly media quota): **14,000 posts take about 5 hours.** While a send wait runs, the linked account cannot send messages in the Telegram
app either. Details and the measurements: [docs/TELEGRAM_LIMITS.md](docs/TELEGRAM_LIMITS.md).

## Configuration

All settings are environment variables (see `.env.example`).

| Variable | Default | |
|---|---|---|
| `BOT_TOKEN` | - | token from @BotFather |
| `ADMIN_IDS` | - | comma-separated numeric Telegram ids allowed to use the bot |
| `CLAIM_CODE` | empty | if set, anyone sending `/start <code>` becomes an admin |
| `TG_API_ID`, `TG_API_HASH` | empty | Telegram app credentials; enable the Premium engine |
| `USER_SESSION` | `data/user` | session file of the linked account (**secret**) |
| `USER_MIN_INTERVAL` | `0.8` | seconds per post the account writes |
| `USER_BATCH_SIZE` | `20` | posts per window (Premium engine) |
| `PREMIUM_EMOJI_FILE` | bundled | ordinary → Premium emoji map ([docs](docs/PREMIUM_EMOJI.md)) |
| `DB_PATH` | `data/chanmover.db` | SQLite database |
| `BATCH_SIZE`, `BATCH_DELAY` | `20`, `1.0` | bot engine window size / pause |
| `LOOKAHEAD`, `SCRAPE_DELAY` | `600`, `0.35` | bot engine: private-source scan, preview paging |

## Good to know

* The copy has **today's date** on every post (Telegram does not allow changing it). Keep the destination private until the job is done.
* Sources with *Restrict saving content* cannot be copied; posts Telegram blocks for everyone (copyright) are skipped and counted.
* Inline buttons: URL buttons are carried over; callback buttons only work inside their own bot, so they are reported, not copied.
* Links to a **private** destination look like `t.me/c/<id>/<post>` and open only for its members.
* Editing posts afterwards is possible but Telegram allows only ~4-5 edits a minute - that is why links are written at copy time.
* Use it on channels you own or administer and with an account you own. See [SECURITY.md](SECURITY.md) for what is secret.

## Documentation

* [Architecture](docs/ARCHITECTURE.md) - jobs, windows, engines, link prediction, crash safety
* [What Telegram allows](docs/TELEGRAM_LIMITS.md) - measured limits and why the design is the way it is
* [Operations](docs/OPERATIONS.md) - service, upgrades, flood waits, recovery
* [Premium emoji map](docs/PREMIUM_EMOJI.md)

## Development

```bash
make install && make lint && make test
```
Telegram is faked in the tests, so no token or network is needed. Contributions are welcome: [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). The bundled emoji map only contains public Telegram custom-emoji ids.
