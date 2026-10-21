# Security policy

## Reporting a vulnerability

Please report security problems **privately** through
[GitHub security advisories](https://github.com/alzhmd/tg-channel-mover/security/advisories/new) instead of a public issue.
Include what you found, how to reproduce it and, if you can, a suggested fix. You will get an answer within a few days.

## What is sensitive

* `BOT_TOKEN` - full control of the bot.
* The Premium-account **session file** (`USER_SESSION`, default `data/user.session`) - it is a logged-in Telegram device of that
  account. Treat it like a password: keep `data/` readable only by the service user (the shipped systemd unit and Dockerfile do),
  never commit it, never share it. If it leaks, remove the "Channel Mover" device in *Telegram -> Settings -> Devices*.
* `TG_API_ID` / `TG_API_HASH` - your Telegram application credentials.
* The SQLite database - contains channel ids and message-id maps, no credentials.

Only the Telegram user ids listed in `ADMIN_IDS` (plus users who used the optional `CLAIM_CODE`) can control the bot.
The bot deletes the message in which a 2FA password is sent, but a password is still sent through Telegram: if that bothers
you, temporarily turn two-step verification off while linking, or use the QR link only.

## Supported versions

Only the latest release receives fixes.
