# Architecture

```
            ┌────────────┐  buttons / texts   ┌──────────────────────────────────────────┐
  you ────▶ │  Telegram  │ ◀────────────────▶ │ handlers.py  premium_ui.py  ui.py (Persian)│
            └────────────┘                    └───────────────┬──────────────────────────┘
                                                              │ jobs, options
                                              ┌───────────────▼──────────────┐
                                              │ engine.py  (job runner)      │── SQLite (db.py)
                                              │  plan → windows → copy       │   jobs, msgmap, windows,
                                              └───────┬──────────────┬───────┘   failures, live_queue, kv, fixes
                                         Bot API      │              │ MTProto (Telethon)
                                  ┌───────────────────▼──┐   ┌───────▼────────────────────────┐
                                  │ copyMessages         │   │ userengine.py (Premium account)│
                                  │ scrape.py (t.me/s)   │   │ emoji.py  links.py  linkfix.py │
                                  └──────────────────────┘   └────────────────────────────────┘
```

## Jobs

A *job* copies one source channel into one destination channel with a fixed set of options. It moves through
`scanning → running → done` (or `paused`, `error`, `cancelled`) and is stored in SQLite after every window, so a restart continues
exactly where it stopped.

### Plan and windows

* **Bot engine** - the Bot API cannot list a channel, so the plan is a range of ids (`RangePlan`). For a public source the newest id
  and the ids the web preview shows (`scrape.py`) make the range exact and the old→new pairs precise; a private source is walked
  until `LOOKAHEAD` empty ids pass the newest post found. The preview lists an album only by its first id, so it is *never* used as the
  list of ids to copy.
* **Premium engine** - the account reads the real list of posts (`UserBackend.scan`): ids, album spans and posts Telegram blocks for
  everyone (`restriction_reason`, e.g. copyright). The plan (`ListPlan`) never splits an album between two windows.

A *window* is a batch of ids copied together and recorded in one SQLite transaction (`Database.record_batch`).

### Copying

* **Bot engine**: `copyMessages` per window; on an unexplained error the window is split until the bad post is isolated; posts with
  URL buttons are copied one by one with `reply_markup` (Telegram drops buttons in copies).
* **Premium engine** (`UserBackend.copy_window`): messages are fetched fresh (media references expire), consecutive posts that need no
  change are forwarded in one `ForwardMessages(drop_author)` call, posts that need new emoji or links are re-sent with their media by
  reference (`send_file`/`send_message`, albums keep their group), and posts with URL buttons are handed to the bot.

### Exact old → new ids

`msgmap(job_id, src_id, dst_id)` holds every pair. The Premium engine maps pairs from the `UpdateMessageID` entries of each response,
so it is always exact. The bot engine can only pair a window when nothing in it is missing; otherwise the window is stored in `windows`.

## Links to the old channel (`links.py`, `linkfix.py`)

Copied posts still link to `t.me/OldChannel/123`. Rewriting needs the *new* id of post 123 - including posts that have not been copied
yet. A channel hands out ids one after another, so with a throw-away probe post giving `base`, the k-th post of the plan will become
`base + 1 + k`. Every window is checked against that prediction; on the first mismatch the job records what was sent and stops with an
error, because later links would be wrong. Former usernames of the old channel are supplied by the owner (`link_names`).

Editing links afterwards (`LinkFixer`) works but Telegram allows only ~4-5 edits per minute (`docs/TELEGRAM_LIMITS.md`), so it is meant for
small repairs. Live posts are re-pointed while they are copied, using the actual map.

## Flood control (`Pacer`)

The account writes at `USER_MIN_INTERVAL` seconds per post (with jitter). Each `FLOOD_WAIT` is waited out exactly, doubles the interval
(×4 after a wait ≥ 5 min, capped at ×8) and the interval eases back 0.5 % per write. The end of the last wait is stored in `kv`, so a
restart never skips it, and for two hours after a flood the pace starts at a quarter speed. The owner is told about long waits.

## Crash and restart safety

* One SQLite transaction per window; a graceful stop (`post_stop`) lets the window in flight finish.
* If a window fails half way, the posts already sent are recorded (`e.partial`) and skipped on retry (`Database.mapped`).
* `resume_all()` continues `running` jobs after a restart; `paused` and `error` jobs wait for the owner.
* A hard kill (SIGKILL, power loss) can leave posts that were sent but not recorded - see `docs/OPERATIONS.md`.

## Live mirror

The bot receives `channel_post` updates for channels it administers. New posts are queued (`live_queue`) while the history is copied and
drained afterwards in order, albums grouped; a post the history pass already covered is recognised through the map and skipped.

## Testing

Everything that touches Telegram is behind small interfaces; `tests/conftest.py` fakes the Bot API (`FakeBot`), the Premium account
(`FakeUser`) and the web preview. Real-Telegram behaviour that the fakes cannot prove is documented in `docs/TELEGRAM_LIMITS.md`.
