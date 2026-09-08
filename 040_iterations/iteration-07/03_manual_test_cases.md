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
| MT-10 | The feed delivers | OpenAlgo up, broker session live, market open | Run the `PriceFeed` snippet from §11 of the implementation guide against a liquid NIFTY weekly option | A `Tick` with a positive price and `source="websocket"` within two seconds; the log line `price feed subscribed to …` appears once. | AC-2, AC-12 |
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
