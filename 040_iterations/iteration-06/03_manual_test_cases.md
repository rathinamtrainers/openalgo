# Iteration 06 — Manual Test Cases

These are the checks you run with your own hands before the suite in `04_test_automation.md`
runs them forever and before `05_deployment_guide.md` releases the code to the trading host.

This slice is unusual: the thing it builds is a click, and a click cannot be faked convincingly
by a test double. So the important block here is the one where you sit in front of OpenAlgo's
Action Center with the desk running beside it. All of it can be done safely, because OpenAlgo's
**analyze (sandbox) mode leaves the semi-auto queue exactly where it is** — the routing check in
`services/place_order_service.py` runs before the analyze branch — so the whole path including
the approval is exercisable against ₹1 crore of sandbox capital with no broker risk and no
money at stake.

| Block | Needs | When you can run it |
| --- | --- | --- |
| **A — off-host** | The repo and `uv`. Nothing else. | Now. Any day, any hour. |
| **B — a local OpenAlgo** | OpenAlgo running locally, its API key in semi-auto, analyze mode on. | Any day. Market hours only for MT-09 onwards. |
| **C — on the trading host** | A deployed release and a live broker session. | After §6 of the deployment guide. |

## How to run these

Work in a scratch state directory so nothing here touches the host journal, and point the
mirror at your local OpenAlgo:

```bash
cd strike_desk
export STRIKE_DESK_OPENALGO_API_KEY='<the key from /apikey>'
export STRIKE_DESK_STATE_DIR="$PWD/.state-manual"
export STRIKE_DESK_EXECUTION_ENABLED=true
export STRIKE_DESK_OPENALGO_USER='<your OpenAlgo username>'
export STRIKE_DESK_OPENALGO_DB_PATH="$PWD/../db/openalgo.db"
mkdir -p "$STRIKE_DESK_STATE_DIR"
uv sync
```

Block A drives the pure functions directly, because pricing and intent building are arithmetic
and calling them is the shortest path from a case to its answer:

```python
from datetime import UTC, datetime
from strike_desk.approval_gate import TickContext, align_price, build_intent
from strike_desk.config import get_settings
from tests.approval_fixtures import cleared_context

settings = get_settings()
build_intent(cleared_context(), settings, datetime.now(tz=UTC))
```

Block B needs one trick: getting the desk to produce an `enter` on demand rather than waiting
for a tradeable regime. Use the seam the suite uses — register a stub strategist and a stub
analyst through `tests/approval_fixtures.py`'s `forced_entry_service()`, which builds a real
`StrikeDeskService` whose two specialists return a fixed regime and the recorded contract. The
gate, the client, the mirror, the watcher, the journal and OpenAlgo are all real.

```bash
uv run python -m tests.approval_fixtures forced-entry   # runs one forced entry tick and exits
```

## Block A — off-host

| ID | Title | Preconditions | Steps | Expected result | Covers |
| --- | --- | --- | --- | --- | --- |
| **MT-01** | The limit price is the top of the band on the grid | None | `align_price(150.10, 192.03, 0.05)` | `192.00` — floored to the tick, never above the band | AC-4 |
| **MT-02** | A band narrower than a tick still prices | None | `align_price(192.01, 192.04, 0.05)` then `align_price(192.06, 192.09, 0.05)` | The first raises `UnpriceableBand`; the second also raises — no multiple of 0.05 lies inside either | AC-4 |
| **MT-03** | The quantity is the verdict's, not the model's | None | Build an intent from a context whose proposal says `lots: 2, quantity: 300` but whose verdict cleared 1 lot at lot size 75 | `quantity == 75`, `lots == 1` — the proposal's own quantity is ignored | AC-4 |
| **MT-04** | An unusable intent never reaches the wire | None | Build an intent from a context with `lots_cleared: 0` | `InvalidOrderPayload`, and with the gate wired, an `approvals` row at `submit-failed` and no HTTP call | AC-4 |
| **MT-05** | The execution whitelist is closed | None | `ExecutionClient(settings)._request("/api/v1/closeposition", {}, retries=0)` | `ExecutionPathViolation` raised before any socket opens | AC-5 |
| **MT-06** | The mirror cannot write | A copy of an OpenAlgo database | Open the mirror and run `session.execute(text("UPDATE api_keys SET order_mode='auto'"))` in a scratch script | `OperationalError: attempt to write a readonly database` | AC-13 |
| **MT-07** | The gate fails closed on a missing database | `STRIKE_DESK_OPENALGO_DB_PATH=/tmp/does-not-exist.db` | `uv run strike-desk status` | `approval gate : UNUSABLE — /tmp/does-not-exist.db does not exist` | AC-2, AC-13 |
| **MT-08** | Settling twice writes once | A journal holding one pending approval | Call `settle_approval` twice with the same `Resolution` | First returns `True`, second returns `False`; `SELECT count(*) FROM approvals WHERE status='approved'` is 1 | AC-11 |

## Block B — with a local OpenAlgo in sandbox

| ID | Title | Preconditions | Steps | Expected result | Covers |
| --- | --- | --- | --- | --- | --- |
| **MT-09** | An intent reaches the queue | Semi-auto on, analyze mode on, execution enabled | Run `forced-entry`, then `strike-desk approvals` and open `/orders/action-center` | One `pending` approval naming a `pending_order_id`; the same order visible in the Action Center with strategy tag `strike-desk:<8 hex>`; `strike-desk journal` shows one `enter` decision written **before** it | AC-1, AC-12 |
| **MT-10** | The case is readable before the click | MT-09's approval outstanding | `strike-desk approvals` | The block shows the contract, lots and limit price, the deadline in IST, the risk verdict's cleared size and rupee risk, the regime label with its rationale, and the strategist's case | AC-15 |
| **MT-11** | The desk proposes nothing while it waits | MT-09's approval outstanding | Force a second tick, then `strike-desk journal` | The second tick is `hold / approval-pending` naming the pending order id; no regime read and no proposal row was written for it, and `strike-desk status` shows no new token cost | AC-10 |
| **MT-12** | Approval settles into a chain | MT-09's approval outstanding | Approve in the Action Center; wait 10 seconds; `strike-desk approvals --json` | An `approved` row with your username in `approved_by`, an IST `resolved_at_ist`, a `wait_seconds` matching the clock, and one order entry with a `broker_order_id` and a status; `verdict_id` and `proposal_id` resolve to real rows | AC-6, AC-12 |
| **MT-13** | Rejection keeps the reason | A fresh forced entry | Reject in the Action Center with the reason `strike too far OTM`; wait 10 seconds; `strike-desk approvals` | A `rejected` row whose detail contains `strike too far OTM` verbatim, and no order rows for that approval | AC-7 |
| **MT-14** | An unanswered intent expires | A fresh forced entry, `STRIKE_DESK_APPROVAL_DEADLINE_SECONDS=60` | Do nothing for 70 seconds; read the log and `strike-desk approvals` | An `expired` row with `withdrawal: not-queued`, and one CRITICAL log line naming the pending order id to reject by hand | AC-8 |
| **MT-15** | A late click is caught and cancelled | MT-14's pending order still queued in the Action Center | Approve it now; wait 10 seconds | A second terminal row at `late-approval` marked `defect`, `withdrawal: permitted` (analyze mode allows the cancel), a CRITICAL log line, and an order row showing the cancellation | AC-9, AC-11 |
| **MT-16** | The order is followed to its end | A fresh forced entry approved promptly | Wait for the sandbox to fill it, then `strike-desk approvals --json` | Two order entries for the approval — `open` then `complete` with an `average_price` — and no duplicate at either status | AC-6, AC-11 |
| **MT-17** | An unfilled order is withdrawn | A forced entry priced away from the market, `STRIKE_DESK_FILL_DEADLINE_SECONDS=60` | Approve it and wait 70 seconds | A WARNING naming the age, a cancel attempt, and a final order row at `cancelled` | AC-6 |
| **MT-18** | The gate is refused in auto mode | Switch the API key to **auto** at `/apikey` | Force an entry tick | The tick declines at `plan` with `approval-gate-unavailable`; no regime read, no proposal, no `placeorder` call in OpenAlgo's traffic log; `strike-desk declines` exits 2 | AC-2 |
| **MT-19** | A bypass stops the desk | Auto mode, and the `plan` gate temporarily disabled by setting `STRIKE_DESK_EXECUTION_ENABLED=true` with a stubbed healthy mirror (`tests/approval_fixtures.py::bypass_rig`) | Force an entry tick | A `gate-bypassed` approval row marked `defect`, the kill switch file written, a CRITICAL log line, and every subsequent tick skipped by the session gate | AC-3 |
| **MT-20** | The kill switch reaches the queue | A fresh forced entry outstanding | `strike-desk kill --reason "manual test"`; wait 10 seconds | The approval settles `expired` with the detail naming the kill switch, within one poll | AC-14 |
| **MT-21** | A restart settles what it left | A fresh forced entry outstanding | Stop the service; approve in the Action Center; start the service; wait 10 seconds | One `approved` row and one order row — settled exactly once, with no duplicate and no error in the log | AC-14 |
| **MT-22** | The trace carries the hop | MT-12 done | `sqlite3 $STRIKE_DESK_STATE_DIR/strike_desk.db "SELECT name, attributes_json FROM traces WHERE name IN ('tick.submit','strike_desk.approval','tick.settle');"` | `tick.submit` carries the approval id, pending order id, quantity and limit price; `strike_desk.approval` carries the status and wait; `tick.settle` carries `strike_desk.tick_trace_id` | AC-15 |

## Block C — on the trading host

| ID | Title | Preconditions | Steps | Expected result | Covers |
| --- | --- | --- | --- | --- | --- |
| **MT-23** | The mirror can be read as the service user | The release deployed, execution still disabled | `sudo -u strikedesk /opt/strike-desk/current/strike_desk/.venv/bin/strike-desk status` | `approval gate : ok — semi-auto approval gate is active`. A permissions error here means the group grant in §4 of the deployment guide was missed | AC-2, AC-13 |
| **MT-24** | The first live intent | Execution enabled, analyze mode **off**, market open, a real cleared intent | Watch `strike-desk approvals` when the desk next enters; read the case; approve it | The queued order matches the intent exactly — symbol, quantity and limit price — and the resulting broker order id appears in OpenAlgo's order book and in the `orders` table | AC-1, AC-4, AC-6 |
| **MT-25** | A live rejection is refused a cancel | A live approval expired past its deadline and then approved late | Read the log and `strike-desk approvals` | `late-approval` with `withdrawal: refused` quoting OpenAlgo's semi-auto message, and a CRITICAL line telling you to close the position by hand. This is the limitation named in §13.2 of the implementation guide, observed rather than assumed | AC-9 |
