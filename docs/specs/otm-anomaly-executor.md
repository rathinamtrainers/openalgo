# OTM Bid-Anomaly Direct Executor — OpenAlgo / Strike Desk

This is the btcalgo scanner spec **adapted to this repo**. There is no OTM
anomalies panel here today. We build it in this ecosystem; we do not clone
btcalgo.

**Phase 0 report:** [phase0-report.md](phase0-report.md). Do not start Phase 1
until that report is reviewed.

## Ecosystem compatibility (overrides the original spec where they clash)

| Original spec | This repo |
|---|---|
| btcalgo scanner process + Socket.IO panel | New **out-of-process** unit (same pattern as `strike-desk-coa.service`). Flask/eventlet must **not** run the WS+order loop. |
| `DELTA_API_KEY` / `DELTA_API_SECRET` | `DELTA_INDIA_API_KEY` / `DELTA_INDIA_API_SECRET` (already dedicated so they do not steal the Zerodha `BROKER_API_*` slot). |
| Import signing from OpenAlgo, do not import across repos | Same git repo — **import** `broker.deltaexchange.api.baseurl.get_auth_headers`. Do **not** call `place_order_api` (it retries POST on 429). |
| OpenAlgo placeorder / Tradetron webhook | **Forbidden.** GCE OpenAlgo is a Zerodha session. Orders go to Delta REST only. |
| `l1_orderbook` / `l2_orderbook` on `wss://socket.india.delta.exchange` | Docs deprecate those on **31 Oct 2026**. Use `ob_l1` on `wss://public-socket.india.delta.exchange`. Private fills stay on `wss://socket.india.delta.exchange` (`orders`, `positions`, `v2/user_trades`). |
| Mix with BTC CoA Hands | **No.** CoA BTC Hands stays on Tradetron. This unit never posts Hands flags. |
| Live Auto QuadShield / Nifty tokens | Untouched. |

Ground rules from the original spec still apply: DRY_RUN default, unique
`client_order_id`, no blind order POST retry, Decimal prices, env secrets
never logged, tests per phase.

Original phases 1–11 (settings, `delta_client`, books, validate, PairExecutor,
risk, audit, UI, alerts, replay, rollout) remain the work plan. Field names
and URLs always lose to https://docs.delta.exchange.

`client_order_id` max length in docs is **32**. Format `oa-<pair_id>-<leg>-<attempt>`
must be compacted (short pair id, not a UUID).
