# Contributing

Thanks for helping! Bug reports, ideas, translations of the bot's texts and pull requests are all welcome.

## Set up

```bash
git clone https://github.com/alzhmd/tg-channel-mover.git && cd tg-channel-mover
make install     # virtualenv + dependencies + dev tools
make lint        # ruff
make test        # pytest - Telegram is faked, no network or tokens needed
```

## Ground rules

* Keep the engines honest: anything that talks to Telegram must handle `FloodWait` by waiting the exact time, never retry during
  a wait, and never lose or repeat a post after a crash (see `docs/ARCHITECTURE.md`).
* New behaviour comes with a test. `tests/conftest.py` has fakes for the Bot API and for the Premium account.
* The bot's user interface is Persian today (`chanmover/ui.py`, `handlers.py`, `premium_ui.py`). Making the texts translatable is a
  welcome contribution - open an issue first so we agree on the approach.
* Never commit tokens, session files, phone numbers or real channel ids; use neutral examples.
* Update `CHANGELOG.md` for user-visible changes.

## Pull requests

Small, focused PRs are reviewed fastest. Describe the problem, the approach and how you tested it.
Be kind: this project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
