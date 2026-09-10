# Iteration 07 — Manual Test Cases

These are the checks you run with your own hands before the suite in `04_test_automation.md`
runs them forever and before `05_deployment_guide.md` releases the code to the trading host.

The thing this slice builds is a reflex, and a reflex is judged by two questions a test double
answers badly: does it fire on the right observation, and how long does it take? So the centre
of gravity here is Block B — OpenAlgo in **analyze (sandbox) mode**, a real fill against ₹1
crore of sandbox capital, a real WebSocket feed, and levels you set yourself so an exit fires
while you are watching it. Nothing in this document risks money.

| Block | Needs | When you can run it |
| --- | --- | --- |
| **A — off-host** | The repo and `uv`. Nothing else. | Now. Any day, any hour. |
| **B — a local OpenAlgo in analyze mode** | OpenAlgo running locally with a broker session, its key in semi-auto, analyze mode on, the WebSocket proxy up. | Market hours, so the feed carries ticks. |
| **C — on the trading host** | A deployed release. | After §6 of the deployment guide. |
| **D — unattended mode** | Everything Block B needs, plus the OpenAlgo key switched to **Auto** and `STRIKE_DESK_AUTONOMY=unattended`. Analyze mode stays **on**. | After Block B passes. Never on a live session from a laptop. |

## How to run these

Work in a scratch state directory so nothing here touches the host journal:

```bash
cd strike_desk
export STRIKE_DESK_OPENALGO_API_KEY='<the key from /apikey>'
export STRIKE_DESK_STATE_DIR="$PWD/.state-manual"
export STRIKE_DESK_EXECUTION_ENABLED=true
export STRIKE_DESK_MONITOR_ENABLED=true
export STRIKE_DESK_OPENALGO_USER='<your OpenAlgo username>'
export STRIKE_DESK_OPENALGO_DB_PATH="$PWD/../db/openalgo.db"
mkdir -p "$STRIKE_DESK_STATE_DIR"
uv sync
```

Block A drives the pure evaluator directly, because precedence and the time-stop are arithmetic
and calling them is the shortest path from a case to its answer:

```python
from datetime import UTC, datetime, timedelta
from strike_desk.levels import ExitLevels, evaluate, resolve_time_stop

levels = ExitLevels(
    stop_price=80.0,
    target_price=140.0,
    time_stop_utc=datetime.now(tz=UTC) + timedelta(minutes=30),
    time_stop_reason="time-stop",
)
evaluate(levels, 79.5, datetime.now(tz=UTC))
```

Block B needs one arrangement: a position whose levels sit close enough to the live premium
that an exit fires within a few minutes. The fixture module used by the automated suite writes
a proposal row on demand with levels you choose, so you can adopt a real sandbox fill against a
stop two rupees below the current premium and watch the reflex work:

```bash
uv run python -m tests.position_fixtures seed --stop-offset 2 --target-offset 40
```

## Block A — the evaluator and the preflight, off-host

| ID | Title | Preconditions | Steps | Expected result | Covers |
| --- | --- | --- | --- | --- | --- |
| MT-01 | Stop breach fires | Block A shell | `evaluate(levels, 80.0, now)` and again with `79.0` | Both return a trigger with `reason="stop"`, `level_price=80.0` and the observed price echoed. At-the-level counts as a breach. | AC-3 |
| MT-02 | Target breach fires | Block A shell | `evaluate(levels, 140.0, now)` | Trigger with `reason="target"`. | AC-4 |
| MT-03 | Between levels holds | Block A shell | `evaluate(levels, 110.0, now)` | `None`. No exit, no logging, no I/O. | AC-3, AC-4 |
| MT-04 | Time beats price | Block A shell | Build levels with `time_stop_utc` one minute in the past, then `evaluate(levels, 79.0, now)` | Trigger reason is `time-stop`, not `stop`, even though the stop is also breached. | AC-5, AC-6 |
| MT-05 | Stop beats target | Block A shell | Build levels with `stop_price=140.0` rejected by the constructor; instead call `evaluate` with a price that is simultaneously `<= stop` and `>= target` by using `stop=100, target=100.0001` and price `100` | Trigger reason is `stop`. | AC-6 |
| MT-06 | Impossible levels are refused | Block A shell | `ExitLevels(stop_price=140, target_price=80, …)` | `LevelsUnavailable` is raised at construction; the monitor can never arm on inverted levels. | AC-1 |
| MT-07 | The time-stop takes the earlier clock | Block A shell | `resolve_time_stop("15:40", time(15,10), date.today())` then `resolve_time_stop("14:20", time(15,10), date.today())` | First returns 15:10 IST with reason `session-deadline`; second returns 14:20 IST with reason `time-stop`. | AC-5 |
| MT-08 | A missing time-stop still has a clock | Block A shell | `resolve_time_stop(None, time(15,10), date.today())` | 15:10 IST, reason `session-deadline`. No position is ever without a clock. | AC-1, AC-5 |
| MT-09 | The monitor path imports no agent | Block A shell | `uv run python -c "import strike_desk.position_monitor, sys; print([m for m in sys.modules if 'regime_analyst' in m or 'options_strategist' in m or 'model_client' in m])"` | Empty list. | AC-8 |

## Block B — a real fill, a real feed, a real exit

| ID | Title | Preconditions | Steps | Expected result | Covers |
| --- | --- | --- | --- | --- | --- |
| MT-10 | The feed delivers | OpenAlgo up, broker session live, market open | Run the `PriceFeed` snippet from §12 of the implementation guide against a liquid NIFTY weekly option | A `Tick` with a positive price and `source="websocket"` within two seconds; the log line `price feed subscribed to …` appears once. | AC-2, AC-12 |
| MT-11 | Adoption stamps the levels | Analyze mode on, seeded proposal, an approved sandbox order that filled | Watch the log; then `uv run python -m strike_desk position` | Within one poll of the fill: `managing N x SYMBOL at P — stop … target … time-stop …`. The view prints all three levels with live distances and a `websocket` feed source. | AC-1, AC-2, AC-14 |
| MT-12 | Adoption uses the broker's quantity | As MT-11, with a partially filled sandbox order | Compare the `positions` row's `quantity` with the order's requested quantity and with `/api/v1/positionbook` | The row matches the position book, not the request. | AC-2 |
| MT-13 | A stop fires and is recorded | As MT-11, seeded with a stop two rupees under the premium | Wait for the premium to trade through the stop | An `exits` row with `reason="stop"`, `path="closeposition"`, a `latency_ms` under 1500, the observed price and `feed_source="websocket"`; a `positions` row at `flat`; the sandbox position gone. | AC-3, AC-7, AC-10, AC-14 |
| MT-14 | The time-stop fires regardless of price | Seed a proposal with a time-stop two minutes ahead and a stop far below | Wait past the time-stop | Exit fires with reason `time-stop` while the premium sits between the levels; `positions` reaches `flat`. | AC-5 |
| MT-15 | Slippage is recorded honestly | After MT-13 | Read the `exits` row | `level_price`, `observed_price` and `exit_price` are all present and `slippage = exit_price - level_price`, positive or negative as it fell. | AC-10 |
| MT-16 | Latency is measured, not assumed | After MT-13 | `uv run python -m strike_desk position --json` and the `strike_desk.monitor.exit` span in the `traces` table | The span carries `exit.latency_ms` equal to the row's `latency_ms`, and `exit.path` and `exit.reason` match. | AC-7 |
| MT-17 | A quiet feed falls back to quotes | MT-11 running | Stop the WebSocket proxy (`sudo systemctl stop openalgo` is too blunt — instead block the port: `sudo iptables -I OUTPUT -p tcp --dport 8765 -j REJECT`) and wait 20 seconds | The exit row on the next trigger, and the `position` view meanwhile, report `feed_source="quotes"`. The monitor keeps evaluating. Remove the rule afterwards with `-D` in place of `-I`. | AC-12 |
| MT-18 | A total blackout exits flat | MT-17 with the quotes endpoint also unavailable (stop OpenAlgo entirely for 95 seconds) | Restart OpenAlgo and read the journal | An `exits` row with reason `feed-blackout` was attempted; if OpenAlgo was down the attempt is recorded `failed` and escalation follows MT-20. | AC-12, AC-11 |
| MT-19 | A manual close stands the monitor down | MT-11 running | Close the sandbox position yourself from the OpenAlgo UI | Within one reconcile interval a `positions` row appears at `stood-down` with `exit_reason="manual"`, the feed log shows the subscription closing, and `position` reports nothing under management. No exit order is attempted. | AC-13 |
| MT-20 | A failing exit escalates | MT-11 running, then OpenAlgo stopped just before the stop is hit | Let the stop breach with OpenAlgo down | Three `exits` rows at `failed`, one `CRITICAL` log naming symbol and quantity, a `positions` row at `orphaned` marked a defect, and the kill switch file present. `strike-desk position` exits 2. | AC-11, AC-14 |
| MT-21 | Duplicate states are refused | After any completed position | Re-run the adoption for the same order id via the fixture module's `adopt` entry point | The log says *already adopted*; no second `positions` row exists for that state. | AC-14 |
| MT-22 | An unexplained position is not adopted | Analyze mode on, no desk order today | Open a sandbox position by hand in an option symbol, then start the desk | The monitor logs that the broker reports a position with no order row, records nothing as managed, and never puts a stop on it. | AC-2 |

## Block C — the gate that stops entries

| ID | Title | Preconditions | Steps | Expected result | Covers |
| --- | --- | --- | --- | --- | --- |
| MT-23 | Live semi-auto declines entries | Deployed release, analyze mode **off**, key in semi-auto | Force a tick with `strike-desk tick-now` and read `strike-desk journal` | The tick declines with reason code `exit-path-gated` and a sentence naming the detail. No proposal is formed and no token is spent. | AC-9 |
| MT-24 | Analyze mode reopens the path | As MT-23, then turn analyze mode on | `strike-desk tick-now` again | The tick proceeds normally; no `exit-path-gated` decline. | AC-9 |
| MT-25 | Auto mode also reopens it | Analyze mode off, key switched to Auto at `/apikey` | `strike-desk tick-now` | No `exit-path-gated` decline. Switch the key back to Semi-Auto immediately afterwards — auto mode removes the entry gate UC-06 depends on. | AC-9 |
| MT-26 | The declines report counts it | After MT-23 | `strike-desk declines` | `exit-path-gated` appears with its count, category `system`, disposition `defect`, and the command exits 2. | AC-9, AC-14 |

## Block D — unattended mode, no human in the loop

These cover §9 of the use case and §11 of the implementation guide. **Run every one of them
with OpenAlgo in analyze mode** unless the row says otherwise: unattended mode plus a live
broker session plus Auto order mode is a desk that will place a real order without asking, and
that combination belongs in Block E on the trading host, not on a laptop.

Unattended is the **default** from this iteration on, so MT-27 onwards need no environment
change — an unset `STRIKE_DESK_AUTONOMY` already runs unattended. Set it explicitly only for
MT-29 and MT-39, which exercise the attended opt-out, and unset it again afterwards. The setting
you must check before walking away is no longer the mode but the **order mode on the OpenAlgo
key and analyze mode**, because those are now the only things standing between a default desk
and a real order.

| ID | Title | Preconditions | Steps | Expected result | Covers |
| --- | --- | --- | --- | --- | --- |
| MT-27 | Unattended is the default | Block A shell, no `STRIKE_DESK_AUTONOMY` set | `uv run python -c "from strike_desk.config import get_settings; s=get_settings(); print(s.autonomy, s.unattended)"` | `unattended True`. An `.env` that predates this iteration upgrades straight into autonomous operation — which is the point of the iteration, and why MT-27a exists next to it. | AC-22 |
| MT-27a | An unmigrated key fails loudly, not quietly | Block A shell, no `STRIKE_DESK_AUTONOMY` set, OpenAlgo key still at **Semi-Auto** (an iteration-06 host as it stands today) | `strike-desk tick-now` | Declines with `autonomy-mode-mismatch` and spends no tokens. The upgraded desk neither enters unattended against a gated key nor silently falls back to waiting for a click — it stops until the operator moves the key to Auto. | AC-22, AC-16 |
| MT-28 | The guards agree with the mirror | Block A shell | Call `autonomy.check_mode` with unattended settings and a fake mirror reporting `semi_auto`, then again reporting `auto` | First verdict `ok=False` with reason `autonomy-mode-mismatch` and a detail naming both modes; second `ok=True`. | AC-16 |
| MT-29 | Attended refuses an auto key too | Block A shell | Same call, attended settings, mirror reporting `auto` | `ok=False`, reason `autonomy-mode-mismatch`. The check is symmetric — a desk that thinks it is gated and is not is the more dangerous of the two. | AC-16 |
| MT-30 | The dead-man switch fires | Block A shell | `check_monitor` with unattended settings and (a) `monitor=None`, (b) a stub whose `is_alive()` is `False`, (c) a stub alive but with `heartbeat_utc` 90 seconds old | All three `ok=False`, reason `monitor-unavailable`, details naming the cause. A fresh heartbeat returns `ok=True`. | AC-18 |
| MT-31 | An unattended entry needs no click | Analyze mode on, key at **Auto**, unattended, monitor running | `strike-desk tick-now` on a tick that clears the Risk Officer | A broker (sandbox) order id comes back in the same tick. No `pending_orders` row is created, the Action Center badge does not move, and the following `strike-desk tick-now` does **not** decline with `approval-pending`. | AC-15 |
| MT-32 | The audit trail keeps its shape | After MT-31 | `strike-desk approvals` | The case prints exactly as an attended one does — contract, size, levels, regime read, rationale — with status `auto-approved`, approver `strike-desk` and no deadline. | AC-21 |
| MT-33 | The unattended entry is loud | After MT-31 | `journalctl -u strike-desk -f` or the console, grepping `unattended entry` | One `WARNING` line carrying symbol, quantity, price and all three levels. This is the line the trader reads in the morning. | AC-21 |
| MT-34 | Unattended fills flow straight to the monitor | MT-31's order filled | `strike-desk position` | Within one poll: `managing N x SYMBOL … stop … target … time-stop`, and the view's `autonomy:` line reads `unattended`. The whole path from tick to armed position ran with nobody clicking. | AC-15, AC-18 |
| MT-35 | A queued response is a defect | Unattended, key switched back to **Semi-Auto** *without* restarting the desk, so the cached preflight is stale | Force a tick | Either the preflight catches it and declines with `autonomy-mode-mismatch`, or — if the switch landed between preflight and submit — the response check catches the `pending_order_id`, journals a defect, engages the kill switch and stops the desk. Both are passes; the failure would be an entry sitting silently in a queue. | AC-17 |
| MT-36 | The loss cap stops the day | Unattended, `STRIKE_DESK_UNATTENDED_DAILY_LOSS_CAP=1` | Let one sandbox position exit at any realised loss, then force a tick | The tick declines with `daily-loss-cap`, the detail names the realised figure against the cap, and the kill switch file is present. `strike-desk declines` shows the reason with category `risk` and disposition `expected` — the command does **not** exit 2 for this one. | AC-19 |
| MT-37 | The cap survives a restart | After MT-36, kill switch cleared | Restart the desk and force a tick | It declines with `daily-loss-cap` again. The figure comes from the journal, not from memory, so a restart is not a fresh start. | AC-19 |
| MT-38 | The trade count caps the day | Unattended, `STRIKE_DESK_UNATTENDED_MAX_TRADES_PER_DAY=1`, one entry already taken today | Force a tick | Declines with `daily-trade-cap`, detail naming the count and the cap. | AC-20 |
| MT-39 | The attended opt-out still works | `STRIKE_DESK_AUTONOMY=attended` set explicitly, key back at Semi-Auto | Re-run MT-11 from Block B end to end | Identical to before this section existed: the intent queues, the Action Center badge rises, the tick suspends, and nothing happens until you click. Then **unset** `STRIKE_DESK_AUTONOMY` again so the host is left on the default. | AC-21 |

## What non-determinism looks like here

Nothing in this slice calls a model, so nothing in it is non-deterministic in the sense the
earlier iterations meant. What *is* variable is the market and the network, and it shows up in
three places you should expect rather than chase. Exit **latency** varies with the host's load
and OpenAlgo's own response time — the budget is 1500 ms and a healthy sandbox exit lands well
under 300 ms, but a first call after an idle period can be slower while the HTTP pool warms.
**Slippage** on a market exit is real and can be either sign; a negative number is not a bug.
And the **feed's tick rate** depends on the symbol — a far out-of-the-money strike can go quiet
for tens of seconds in a slow market, which is exactly the condition MT-17 exercises on purpose,
so read `feed_source` before concluding the monitor stalled.

A note on Block D. Unattended mode is the one part of this iteration where a mistake in the
*test setup* costs money rather than a failed assertion: `autonomy=unattended` plus an Auto key
plus analyze mode **off** is a desk that places real orders with nobody watching. Every Block D
row above keeps analyze mode on for that reason. And because unattended is now the default, that
mistake no longer needs a setting to be typed — it needs one to be *forgotten*. Before you stop
paying attention to a host, confirm analyze mode is on, or that the Auto key and the two daily
caps are the ones you meant to leave running.
