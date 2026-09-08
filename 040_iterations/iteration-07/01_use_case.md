# Iteration 07 — UC-07: Manage the open position

> **UC-07 — Manage the open position**
>
> **Why this slice:** the catalog's MVP order runs UC-01 → UC-02 → UC-03 → UC-04 → UC-05 → UC-06 → UC-07, and iterations 01 to 06 built the first six. UC-07 is the next un-built entry by catalog priority, and it is the one the desk cannot honestly go on without: since iteration 06 the trader can click **Approve** and a real broker order can fill, and from that moment the desk owns a live long option with nothing watching it. A position with no stop is not a managed trade, it is an open-ended bet on the clock — the exact bleed the product was built to refuse. This iteration puts the Position Monitor under the fill.
>
> **Actor:** the Position Monitor — deterministic control-plane code, not an agent. The Trader is a spectator here and an escape hatch: he can close the position himself and the monitor will notice and stand down.
>
> **Preconditions:** the iteration-06 desk running, journal at `SCHEMA_VERSION = 6`, taxonomy `dt-4`, playbook `pb-1`, limits `rl-1`, `STRIKE_DESK_EXECUTION_ENABLED=true`, and OpenAlgo's WebSocket proxy reachable on `ws://127.0.0.1:8765` with its ZeroMQ bus up. · **Depends on:** UC-04's `proposals` row, which is where the stop, the target and the theta-derived time-stop were written at proposal time; UC-06's `orders` row and its watcher, which is what tells the monitor a fill happened; and UC-01's journal, span and kill-switch plumbing, which this slice extends rather than reshapes. Telegram delivery of the operator-facing escalations is UC-14's work, so this slice escalates to the process log at `CRITICAL` and to the kill switch.

This iteration builds exactly one thing: a loop that holds a filled long option from fill to flat against three levels it did not choose, and gets out the moment one of them is hit. It adopts the position, arms the levels, watches the price, fires a market exit, records what happened with the latency it took, reconciles against the broker, and stands down. Nothing beyond that.

## 1. The division of labour, restated for the control plane

Iteration 04 established that the model proposes and code verifies. This slice states the same idea one step further down: **the agentic layer contributed the levels at entry time; enforcing them is arithmetic.** The stop, the target and the time-stop were fixed the moment the Options Strategist's proposal cleared `playbook.check` and the Risk Officer; the monitor never re-derives, re-negotiates or re-reasons about them. It compares two numbers and a clock, and when the comparison says get out, it sends a market order.

That is why nothing in this slice imports the reasoning plane. No model call, no MCP tool call, no prompt and no LangGraph node sits between a breached stop and an exit order — an exit that waits on a language model is not an exit. The plan → act → observe loop the rest of the product runs does not appear here at all, and its absence is the design. What this slice does own operationally is the other half of the AgentOps story: **every state change and every exit attempt is a span and an append-only row**, and the exit latency from breach to submission is measured on every single exit rather than asserted once in a test.

## 2. The three levels and their precedence

A long option is held against three exits, and the monitor holds all three from the moment of adoption.

The **stop** is the premium level at which the trade is wrong; the proposal wrote it, and the Risk Officer sized the position so that reaching it costs no more than the per-trade cap. The **target** is where the trade is right and the desk takes the money rather than hoping. The **time-stop** is the wall clock the theta budget bought: a long option decays whether or not the thesis is intact, so the monitor closes on the clock regardless of price. Alongside the proposal's own time-stop the monitor holds a **session deadline** — 15:10 IST by default, comfortably inside the exchange's own square-off — and takes whichever of the two comes first, so an intraday desk never carries a decaying position into the close by accident.

When more than one level is breached in the same observation — a gap can cross the stop and the target in one print, and a tick delivered late can arrive after the time-stop — precedence is fixed and safety-first: **time-based exits beat price exits, and the stop beats the target.** Each of those orderings picks the exit that assumes less. Getting out on the clock is never wrong for an options buyer, and recording a target on an observation that also breached the stop is optimistic bookkeeping.

## 3. Main success scenario

1. The approval watcher sees the entry order reach `complete` and hands the monitor the order id, or the monitor finds an unadopted filled order during startup reconciliation.
2. The monitor resolves the chain backwards — order → approval → proposal — and reads the stop, target, time-stop and theta budget from the `proposals` row.
3. It reads `/api/v1/positionbook` and adopts the **actual** net quantity and average entry price the broker reports rather than the quantity it asked for, so a partial fill is managed as what it is.
4. It writes a `positions` row at state `adopted`, then a second at `armed` once the price feed is delivering ticks for the symbol.
5. The price feed subscribes the option symbol on OpenAlgo's WebSocket proxy in LTP mode and pushes every tick into the monitor with the moment it was received.
6. On each tick the monitor evaluates the levels — three comparisons, no I/O — and does nothing when no level is breached.
7. A tick breaches a level. The monitor stamps the breach time, writes a `positions` row at `exiting`, and submits a market exit through the ungated exit path (§4) without waiting for anything or anyone.
8. It records an `exits` row with the reason, the level, the observed price, the feed source and age, and the latency from breach observation to submission.
9. It follows the exit order to a terminal status, reads the fill price, computes slippage against the level and realised P&L against the entry, and writes the final `positions` row at `flat`.
10. The feed unsubscribes and closes, the monitor stands down, and the next scheduled tick finds a flat book and is free to propose again — subject to the day's trade count.

## 4. Exits are never gated, and the desk proves it before it enters

FR-7 says exits must not require approval even in semi-auto: getting out is never gated. OpenAlgo's semi-auto mode, which iteration 06 depends on for entries, is a **user-level** policy — `services/order_router_service.py` queues every `placeorder` for that key, and `services/close_position_service.py` refuses `closeposition` outright with a 403 unless the platform is in analyze (sandbox) mode. Those two facts together mean the exit path is open in exactly two configurations: the platform in **analyze/sandbox mode**, where `closeposition` executes immediately against the sandbox engine and where the MVP's proving week runs, or the order mode set to **auto**.

The monitor therefore proves rather than assumes. Before a tick is allowed to form a new entry at all, the desk runs an **exit-path preflight** against its read-only mirror of OpenAlgo's database: if the platform is live *and* the key is in semi-auto, the exit path is gated and the tick declines with the new reason code `exit-path-gated`. The desk never takes a position it cannot leave without a human. And when an exit does fire it climbs a two-rung ladder: `closeposition` first, which is never queued and closes the desk's own single position, then a **targeted** SELL market order for the exact symbol and quantity, used when the account holds a position the desk did not open and a blanket close would be wrong. If the fallback comes back queued rather than placed, that is an exit sitting in an approval queue — the monitor records it as `gated`, logs `CRITICAL` with the symbol and quantity the trader must close by hand, and keeps trying.

## 5. Alternate and exception flows

**A gap through the stop** is the normal case, not the exception: options gap. The exit is a market order in every case, so a gap changes nothing about the mechanism and everything about the accounting — the exit row records the level, the observation that breached it and the price it actually filled at, and the difference is stored as slippage rather than smoothed away.

**A rejected or failed exit** retries on a short backoff up to the configured attempt limit, climbing the ladder as it goes. When the last attempt fails the monitor logs `CRITICAL` naming the symbol and the quantity, engages the kill switch so no new intent can be formed, and leaves the position to OpenAlgo's exchange-aligned auto square-off — the backstop that exists precisely because it must survive the thing it is backing up.

**A quiet feed** is handled in two stages. When no tick has arrived for `feed_stale_seconds` the monitor falls back to polling `/api/v1/quotes` for the symbol, which is slower but sufficient, and marks its feed source accordingly on every subsequent exit row. If the REST fallback also fails continuously for `feed_blackout_seconds`, the monitor stops guessing and exits at market with reason `feed-blackout`: a position whose price you cannot see is not a position you hold deliberately.

**The trader closes the position himself** — from the OpenAlgo UI, from his broker's app, or by phone. The reconciler, which reads the position book every `reconcile_interval_seconds`, finds the net quantity at zero, writes a `stood-down` row with exit reason `manual`, closes the feed and stops watching. It never attempts an exit for a position that no longer exists, and it never treats a broker-side truth as a mistake.

**A position the desk cannot explain** — the broker reports a holding in an option symbol with no `orders` row behind it — is recorded `orphaned` with a `CRITICAL` log and is not managed. The monitor manages what it opened; adopting a stranger's position and putting a stop on it is exactly the kind of helpfulness a risk system must not have.

**Levels that cannot be resolved** — the proposal row is missing, or its stop and target are absent — mean the monitor is holding a position it cannot manage. It exits at market immediately with reason `levels-unavailable` and marks the row a defect. An unmanaged position is worse than a small realised loss.

## 6. Data touched

The journal moves to `SCHEMA_VERSION = 7` and gains two append-only tables. **`positions`** holds one row per state of one position — `adopted`, `armed`, `exiting`, `flat`, `stood-down`, `orphaned` — carrying the symbol, exchange, product, adopted quantity, entry price and the three levels as stamped, under `UNIQUE(position_id, state)`. **`exits`** holds one row per exit attempt — reason, ladder rung, trigger and submission timestamps, the latency between them, the level and the observed price, the feed source and its age, the resulting status, the broker order id, the fill price, slippage and realised P&L — under `UNIQUE(position_id, attempt)`. As everywhere else in this journal, uniqueness is the idempotence: a duplicate state write raises `AlreadyJournalled` and the caller reads it as *already done*.

Reads are `proposals`, `orders` and `approvals` for the chain behind a fill, and `api_keys.order_mode` plus `settings.analyze_mode` through the read-only mirror for the preflight. Writes go only to Strike Desk's own database. Market data comes from the WebSocket proxy and, in fallback, from `/api/v1/quotes`, which joins the tick client's read-only whitelist. Exits leave through `/api/v1/closeposition` and `/api/v1/placeorder`, which join the execution client's whitelist. No model is called anywhere in this slice, so the day's token cost is unchanged by it.

## 7. Acceptance criteria

1. **AC-1** — Every adopted position carries a stop, a target and a time-stop stamped from its `proposals` row at adoption; a position whose levels cannot be resolved is exited at market immediately with reason `levels-unavailable` and its row marked a defect.
2. **AC-2** — A filled entry order is adopted within one monitor poll interval of reaching `complete`, whether the watcher hands it over or startup reconciliation finds it, and adoption uses the broker's reported net quantity and average price, not the requested ones.
3. **AC-3** — An observed price at or below the stop fires a market exit recorded with reason `stop`.
4. **AC-4** — An observed price at or above the target fires a market exit recorded with reason `target`.
5. **AC-5** — The monitor exits at the earlier of the proposal's time-stop and the configured session deadline regardless of price, with reason `time-stop` or `session-deadline`; no position outlives the earlier of the two.
6. **AC-6** — When several levels are breached in one observation, precedence holds: time-based exits beat price exits, and `stop` beats `target`.
7. **AC-7** — Every exit row carries the measured latency from breach observation to order submission, the same value appears on the exit span, and the deterministic path stays within `exit_latency_budget_ms`.
8. **AC-8** — No module on the monitor path imports the reasoning plane, and no model or MCP call occurs on it — enforced by an automated import and call-surface test.
9. **AC-9** — A tick declines with `exit-path-gated` whenever the exit-path preflight reports that the platform would queue or refuse an exit, so no entry is formed that cannot be exited without a human.
10. **AC-10** — A gap exit records the level, the breaching observation and the actual fill price, and stores the difference as slippage.
11. **AC-11** — A failing exit retries up to `exit_max_attempts` climbing the ladder, and on final failure logs `CRITICAL` naming symbol and quantity and engages the kill switch.
12. **AC-12** — A feed silent beyond `feed_stale_seconds` switches to REST quote polling and every subsequent exit row names the source; a blackout beyond `feed_blackout_seconds` exits at market with reason `feed-blackout`.
13. **AC-13** — A position closed outside the desk is reconciled within one reconcile interval into a `stood-down` row with exit reason `manual`, and the feed is closed.
14. **AC-14** — Every state change is one append-only row, a repeated state write is refused by the unique constraint and surfaces as *already done*, and `strike-desk position` prints the live position, its three levels, the distance to each, the feed source and its age, exiting non-zero when a defect state exists.

## 8. The flow, in one picture

```mermaid
sequenceDiagram
    participant W as Approval watcher
    participant M as Position Monitor
    participant J as Journal
    participant F as Price feed (:8765)
    participant OA as OpenAlgo /api/v1/
    participant B as Broker

    W->>M: order complete — adopt(order_id)
    M->>J: read proposals · orders · approvals
    M->>OA: positionbook (actual qty, avg price)
    M->>J: positions row — adopted, levels stamped
    M->>F: subscribe LTP for the option symbol
    F-->>M: tick (ltp, received_at)
    M->>J: positions row — armed
    loop every tick
        M->>M: evaluate(stop, target, time-stop) — no I/O
    end
    F-->>M: tick breaching the stop
    M->>J: positions row — exiting
    M->>OA: closeposition (never queued)
    OA->>B: market exit
    M->>J: exits row — reason, level, latency, feed source
    M->>OA: orderstatus until terminal
    M->>J: exits row — fill, slippage, realised P&L
    M->>J: positions row — flat
    M->>F: unsubscribe and close
```
