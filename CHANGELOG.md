# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] - 2026-10-22

First stable release, proven on a real 14,000-post channel (two complete copies, ~2,000 links re-pointed, 81 albums).

### Added
- **Bot engine**: server-side `copyMessages` in windows, no sender name / "Forwarded from", albums and captions kept,
  automatic flood-wait handling, URL inline buttons restored from the public preview.
- **Premium-account engine** (Telethon): keeps Premium emoji and upgrades ordinary emoji to Premium emoji, forwards unchanged
  posts without the author (100 per call), re-sends changed ones with media by reference, exact old to new id map. The account is
  linked from the bot with a QR code (optional 2FA password).
- **Links to the old channel are rewritten while copying**: the destination hands out ids one by one, so the id of a post that is
  not copied yet is predicted and every window is verified against the prediction (the job stops on a mismatch).
- Persian button UI: new-transfer wizard, source/destination by list, `@name`, public link, private invite link or forwarded post,
  **from-post / to-post limits**, live progress (0-100 %, speed, ETA), pause / resume / cancel, retry of failed posts.
- Live mirror of new posts of the source (copied after the history, links re-pointed).
- CSV export of the old to new post ids; "skipped" explanation and list; owner Premium probe; undo (delete exactly what a job created).
- Safe pace for the Premium account (`USER_MIN_INTERVAL`), adaptive back-off after every flood wait, flood waits persisted across
  restarts, notice to the owner on long waits.
- Resume after restarts and crashes (SQLite, one transaction per window, partial windows are recorded and never re-sent).
- Optional slow in-place link fixer (Telegram allows only ~4-5 edits per minute, see `docs/TELEGRAM_LIMITS.md`).
- Docker image, compose file, systemd unit, CI (ruff + pytest on Python 3.11-3.13).
