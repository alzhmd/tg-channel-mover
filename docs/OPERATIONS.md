# Operations

## Install as a service

```bash
sudo cp deploy/channel-mover.service /etc/systemd/system/    # edit WorkingDirectory / ExecStart first
sudo systemctl enable --now channel-mover
journalctl -u channel-mover -f
```
or `docker compose up -d --build`. Keep `.env` and `data/` readable by the service user only.

## Upgrading

1. Open the bot → *Transfers*: if a job is `running`, either wait for it or pause it first.
2. **Never restart during a Telegram flood wait** - a restart resumes the job immediately and requests sent during a wait make it longer.
   The bot stores the end of the last wait and holds back after a restart, but waiting is still the safe choice.
3. `git pull && pip install -r requirements.txt && systemctl restart channel-mover`. A normal restart finishes the window in flight
   (up to 60 s) and continues the job; the database migrates itself.

## Flood waits

They are normal. The bot tells you about waits of 5 minutes or more and continues by itself. While a send wait runs, the linked account
cannot send messages from the Telegram app either. A job that ends in `error` (for example "bot was kicked") continues with
*Resume* after you fix the cause.

## After a hard kill

If the process was killed (SIGKILL, power loss) in the middle of a window, posts that were sent but not yet recorded may exist in the
destination. Before resuming, delete the destination posts whose id is higher than the highest `dst_id` in `msgmap` for the job
(read it with `sqlite3 data/chanmover.db "select max(dst_id) from msgmap where job_id=N"`); the window is then sent again cleanly.
Link prediction would notice a shifted numbering and stop the job instead of writing wrong links.

## Undo a transfer

*Transfers → the job → 🧹* deletes exactly the posts the job created (from the map), never anything else, and closes the job.

## Backups

Back up `data/chanmover.db`. The Premium-account session (`data/user.session`) can be recreated by linking again.

## Removing the account

*Main menu → 🌟 Premium account → Disconnect* logs the session out. You can also remove "Channel Mover" under *Telegram → Settings → Devices*.
