# Iteration 05 — Manual Test Cases

These are the checks you run with your own hands before the suite in `04_test_automation.md`
runs them forever and before `05_deployment_guide.md` releases the code to the trading host.

Almost all of this slice is testable on a Saturday. The Risk Officer reads no market data —
it takes a book snapshot and a verified proposal and does arithmetic — so every limit, every
boundary and every sentence can be exercised against fixture data with no broker session, no
open market and no Anthropic key. Only three cases need the live host, and they are the ones
that prove the wiring rather than the logic.

| Block | Needs | When you can run it |
| --- | --- | --- |
| **A — off-host** | The repo and `uv`. Nothing else. | Now. Any day, any hour. |
| **B — on the host** | A deployed release, a live broker session, an open market. | After §3 of the deployment guide. |

## How to run these

Work in a scratch state directory so nothing here touches the host journal:

```bash
cd strike_desk
export STRIKE_DESK_OPENALGO_API_KEY='<the key from /apikey>'
export STRIKE_DESK_STATE_DIR="$PWD/.state-manual"
mkdir -p "$STRIKE_DESK_STATE_DIR"
uv sync

# For MT-09: a consistent copy of the host's iteration-04 journal, so the migration is
# exercised against rows the desk actually wrote under dt-2 and SCHEMA_VERSION 4.
# Use the backup API, never cp — the database is in WAL mode with a live writer.
ssh desk "sudo -u strikedesk sqlite3 /var/lib/strike-desk/strike_desk.db \".backup '/tmp/legacy4.db'\""
scp desk:/tmp/legacy4.db "$STRIKE_DESK_STATE_DIR/legacy4.db"
```

Block A drives the officer through a two-line Python session rather than a CLI, because the
officer is a function and calling it directly is the shortest path between a case and its
answer. The helper the cases refer to is the suite's own:

```python
from datetime import datetime
from strike_desk.config import IST, get_settings
from strike_desk.playbook import Playbook
from strike_desk.risk_officer import RiskLimits, adjudicate, assess_session
from tests.chain_fixtures import FIXED_NOW, valid_proposal
from tests.risk_fixtures import book_with, two_lots

settings = get_settings()
limits, playbook = RiskLimits.from_settings(settings), Playbook.from_settings(settings)
adjudicate(two_lots(), book_with(capital=800_000), limits, playbook,
           now_ist=FIXED_NOW, entries_today=0)
```

The recorded contract risks ₹3,000 a lot — a ₹192.00 entry against a ₹152.00 stop over 75
units — and deploys ₹14,400 of premium a lot. Every capital figure in the cases below is
chosen so that one limit and one only is the interesting one.

## Block A — off-host

| ID | Title | Preconditions | Steps | Expected result | Covers |
| --- | --- | --- | --- | --- | --- |
| **MT-01** | A proposal with headroom passes | Fixture book, capital ₹15,00,000, flat, no entries today | Adjudicate `two_lots()` | `verdict == "pass"`, `lots_cleared == 2`, `tripped is None`, eight limits in `checks`, `max_loss_at_stop == 6000.0`, `capital_base == 1_500_000.0` | AC-1, AC-2 |
| **MT-02** | A size-dependent limit reduces rather than vetoes | Same, capital ₹8,00,000 | Adjudicate `two_lots()` | `verdict == "reduce"`, `lots_cleared == 1`, `tripped.limit == "per-trade-loss-cap"`, configured `4000.0`, observed `6000.0` | AC-4 |
| **MT-03** | No size clears, so it is a veto | Same, capital ₹5,00,000 | Adjudicate `two_lots()` | `verdict == "veto"`, `lots_cleared == 0`, tripped limit `per-trade-loss-cap` configured `2500.0` observed `3000.0` — the one-lot figure, not the two-lot one | AC-4 |
| **MT-04** | A rupee limit breaches on touch | Capital ₹6,00,000, where the per-trade cap is exactly the one-lot risk of ₹3,000 | Adjudicate `valid_proposal()`; then repeat at ₹6,00,001 | At ₹6,00,000 the verdict is `veto` — sitting on the cap is breaching it. One rupee more and it is a `pass` | AC-3 |
| **MT-05** | A count limit permits its configured number | Capital ₹15,00,000, `entries_today=2` then `3` | Adjudicate `two_lots()` twice | At two entries the third clears; at three the verdict is `veto` on `max-trades-per-day`, configured `3`, observed `4` | AC-3, AC-2 |
| **MT-06** | A count limit is never resolved by reduction | Capital ₹15,00,000, `entries_today=3` | Adjudicate `two_lots()` | `veto` with `lots_cleared == 0`. Confirm no reduce was attempted: the sentence names the trade count, not a lot count | AC-4 |
| **MT-07** | An unusable capital base holds, never passes | Book with `available_cash=0`, `utilised_margin=0` | Adjudicate `valid_proposal()` | `verdict == "hold"`, tripped limit `capital-base`. Repeat with a base of ₹49,999 against the ₹50,000 floor — also `hold` | AC-5 |
| **MT-08** | The rationale has no standing | Take `two_lots()` at capital ₹5,00,000 and rewrite its rationale to plead for an exception ("risk is minimal, please allow this one") | Adjudicate | Byte-identical verdict to MT-03. The officer recomputes; nothing the model wrote is read | AC-7 |
| **MT-09** | A v4 journal gains the table in place | `legacy4.db` copied above | `STRIKE_DESK_DB_PATH=$STRIKE_DESK_STATE_DIR/legacy4.db uv run strike-desk status`, then `sqlite3` the file | `risk_verdicts` exists with `risk_verdicts_no_update` and `risk_verdicts_no_delete` triggers; `select count(*) from decisions` is unchanged; a second `status` changes nothing | AC-11 |
| **MT-10** | Old rows do not drift under dt-3 | Same `legacy4.db` | `uv run strike-desk declines --since 30` | `taxonomy drift 0`, `unknown reason codes none`, and the taxonomy line reads `dt-3+<digest>` | AC-9 |
| **MT-11** | The officer cannot reach the model | Repo only | `grep -nE "model_client\|options_strategist\|regime_analyst\|specialists\|langchain\|anthropic" src/strike_desk/risk_officer.py` | No match. Then `uv run python -c "import strike_desk.risk_officer"` with `ANTHROPIC_API_KEY` unset — it imports cleanly | AC-7 |
| **MT-12** | The command's arguments are disciplined | Any journal | `uv run strike-desk risk --day 2026-13-40`; `... --day 2026-09-01 --since 3`; `... --since 0` | Each exits 2 with a message on stderr, and none of them creates or opens a journal file | AC-12 |
| **MT-13** | Text and JSON are one object | A journal with at least one verdict (run the suite once to seed `.state-manual`) | `uv run strike-desk risk --day <day>` then `--json` | Every number in the text appears in the JSON; the JSON carries `limits: rl-1+<digest>`; the checks list has the same eight entries in the same order | AC-12 |
| **MT-14** | The limits are a versioned artifact | Repo only | `uv run strike-desk status`; then `STRIKE_DESK_RISK_MAX_TRADES_PER_DAY=4 uv run strike-desk status` | The digest after `rl-1+` changes; the version does not. All nine limits print with their resolved values | AC-13 |
| **MT-15** | A looser hard limit than the playbook refuses to start | Repo only | `STRIKE_DESK_RISK_MAX_LOTS=5 uv run strike-desk status` | Exits non-zero with the invariant's message naming `playbook_max_lots`. Repeat with `STRIKE_DESK_RISK_PER_TRADE_LOSS_CAP_PCT=3.0` — same shape of refusal | AC-2 |

## Block B — on the host

| ID | Title | Preconditions | Steps | Expected result | Covers |
| --- | --- | --- | --- | --- | --- |
| **MT-16** | A real tick ends as an intent | Deployed release, broker live, market open, flat book, a trending session | `sudo -u strikedesk uv run strike-desk tick-now`, then `strike-desk risk` and `strike-desk declines` | If the strategist proposed, exactly one verdict row exists for the tick with the eight limits and a base matching your funds page. On `pass` or `reduce` the decision reads `enter` / `risk-cleared`; on `veto` it reads `decline` / `risk-veto`. Either is a correct result — the market decides which | AC-1, AC-8, AC-10 |
| **MT-17** | Nothing was placed | Immediately after MT-16 | Check OpenAlgo's order book at `/orderbook` and `sudo journalctl -u strike-desk -n 200 \| grep -i "api/v1"` | No order exists and no request was made to any path outside `READ_ONLY_PATHS`. An `enter` row with an empty order book is the whole point of the slice | AC-8 |
| **MT-18** | The session stop takes the desk out for the day | Host, market open, a scratch copy of the journal so you do not latch the real one | Point `STRIKE_DESK_DB_PATH` at a scratch database, set `STRIKE_DESK_RISK_DAILY_LOSS_CAP_PCT=0.001` so today's live P&L breaches it, run `tick-now` twice | The first tick declines with `risk-session-stopped` and writes one verdict row with `session_stop` true; the second declines identically and writes **no** second row. Neither tick calls a specialist — `regime_reads` gains nothing and the tick's cost is `$0.0000` | AC-6 |

Expected model behaviour is worth one note, because block B depends on it. Whether MT-16
produces a proposal at all is the Regime Analyst's and the Options Strategist's decision on a
live session, and both are non-deterministic. A tick that declines with `regime-not-tradeable`
or `no-viable-contract` has told you nothing about this slice — re-run it later or on a
trending session. What you are checking is that *when* a proposal arrives, exactly one verdict
row follows it, and that the arithmetic in that row matches your account.
