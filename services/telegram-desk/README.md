# ZeroTouchAlgo desk → Telegram

A small service that watches the Strike Desk live feed and posts to a Telegram channel when a board's chip flips. Every event is written to an append-only, hash-chained ledger **before** it is posted, so the record can be checked by anyone and cannot be quietly edited.

## What it posts

| Post | When |
| --- | --- |
| Map update | Chip flips to CALL TAGGED or PUT TAGGED (in session, before 15:20, within the daily limit and cooldown) |
| Map closed | A tagged board returns to WATCHING or flips to the opposite tag |
| Daily recap | Once at 15:35 on trading days: every tag in order, event counts, and the day's head hash |

It never posts on stale data (no update for 60 s), outside 09:15–15:30 IST, on weekends or listed holidays. If the ledger fails its integrity check, the recap is withheld and a notice is posted instead.

Default wording is index-level map state only, with the disclaimer on every post. The desk's own sentence stays off (`INCLUDE_DESK_NOTE=false`) until a securities lawyer approves it.

## Setup (about 20 minutes)

1. **Create the bot.** In Telegram, message `@BotFather` → `/newbot` → name it (for example "ZeroTouchAlgo Desk") → copy the token.
2. **Create the channel.** New Channel → Public → username such as `@ZeroTouchAlgoDesk`. Use a channel, not a group: it is one-way, like an announcement room.
3. **Add the bot as a channel admin** with permission to post messages only.
4. **Live JSON.** `/ltp` loads `https://zerotouchalgo.com/public-brief.json`. The bot polls the desk copy of the same Nifty object so a chip flip is not waiting on KV: `LIVE_URL=https://desk.zerotouchalgo.com/public-brief.json`. Fields: `scenario_label` (chip), `last`, `computed_at` (ISO 8601 `+05:30`), `line` (sentence, not posted while `INCLUDE_DESK_NOTE=false`).
5. **Match the feed shape.** `extract_boards()` already reads that Strike Desk payload. Live boards: `BOARDS=nifty,banknifty,sensex,btc-options` (BTC is 24/7 via `ALWAYS_OPEN_BOARDS`).
6. **Install** on the Strike Desk host (`strike-desk` in `ztartcopenalgo`):
   ```bash
   sudo bash services/telegram-desk/install.sh /path/to/services/telegram-desk
   sudo -u zta-desk bash -c 'set -a && . /opt/zta-desk/config.env && set +a && python3 /opt/zta-desk/zta_telegram_bot.py --test-post'
   ```
   Runs as user `zta-desk`, data in `/var/lib/zta-desk`, config `0600`. Keep `DRY_RUN=true` for the first session.
   Ledger HTML is at `https://desk.zerotouchalgo.com/ledger` (nginx, read-only). Daily copies go to `/var/backups/zta-desk/ledger/`.
   Health: `zta-desk-health.timer` messages `OWNER_CHAT_ID` (private) if `state.json` is older than 10 minutes during cash hours. Journald already rotates the unit log.
7. **Test the channel:**
   ```bash
   cd /opt/zta-desk && set -a && . ./config.env && set +a
   python3 zta_telegram_bot.py --test-post
   ```
8. **Run one session in dry run** (`DRY_RUN=true`) and read the log. Then set `DRY_RUN=false`.

## Run as a service (systemd)

Unit file is `zta-desk-telegram.service` in this folder (non-root `zta-desk`, `ProtectSystem=strict`).

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zta-desk-telegram
journalctl -u zta-desk-telegram -f
```

## Checking the ledger

```bash
python3 zta_telegram_bot.py --verify
```

Prints `OK: N events verified` or the exact line where the chain breaks. Daily files live in `DATA_DIR/ledger/YYYY-MM-DD.jsonl`. Publishing each day's file alongside the posted head hash lets anyone confirm nothing was edited later.

## Notes

- Restarts are safe: state is saved, and the first reading after a fresh start only sets a baseline — it never posts.
- Tags that fall outside the limits are still written to the ledger and appear in the recap; they just aren't pushed.
- Add a board to `BOARDS` only when its CoA is genuinely live on the desk.
