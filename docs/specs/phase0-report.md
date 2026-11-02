# Phase 0 — OTM anomaly executor discovery (this repo)

Read-only survey of OpenAlgo + Strike Desk. There is **no** existing OTM
bid-anomaly scanner, auto-trade path, or “market fill gap” executor to replace.

## 1. Where things live

| Spec item | This repo |
|---|---|
| WS feed loop | `broker/deltaexchange/streaming/delta_websocket.py` (`DeltaWebSocket.connect`, threading + `websocket-client`). Adapter: `delta_adapter.py`. **Not** subscribed to BTC option chains on GCE (Zerodha session). Strike Desk public REST only: `strike_desk/src/strike_desk/delta_india.py`. |
| Order-book / bid-ask store | Adapter `last_values` + ZMQ proxy. Ticker stores `best_bid` / `best_ask` **without sizes**. L2 has price+size but is the **legacy** channel. No per-update exchange timestamp kept on ticker. |
| Anomaly detection | **Absent.** No `far_bid > near_ask` math. Calendar-spread UI (`services/arbitrage_service.py`) is unrelated. Generic candle scanner doc (`docs/scanner-architecture.md`) is Redis/Historify, unused here. |
| Auto-trade executor | **Absent.** Delta orders today: `broker/deltaexchange/api/order_api.py` `place_order_api` → OpenAlgo `/api/v1/` when the **logged-in broker is Delta**. GCE is Zerodha, so that path cannot place BTC option orders. |
| Risk rails | OpenAlgo sandbox / action-center / strategy risk plans. Nothing for a Delta options pair book. |
| SQLite audit | OpenAlgo uses 6 DBs + NullPool (`database/engine_factory.py`). Strike Desk has its own journal SQLite. No `pairs` / `orders` / `rejects` tables for this product. |
| Settings persistence | Flask session + various SQLite settings tables. No OTM panel settings. |
| Socket.IO sync | `extensions.socketio` in the **gunicorn eventlet** process. Out-of-process units (CoA) publish JSON files Flask then serves. |
| REST poll fallback | Pattern exists (WhatsApp, CoA briefs). Nothing for anomalies. |

## 2. Concurrency model

Production (`install.sh` / Docker): **Gunicorn + eventlet, `-w 1`**. No
`asyncio.run` inside that process. Dev (`uv run app.py`): stdlib threads.

Delta plugin WS is **threads**. CoA live is a **separate systemd unit**
(`strike-desk-coa.service`, `python -m strike_desk.coa_main`).

**Decision:** the executor + scanner must be a new OS process (threads, one
shared httpx client), not a green thread inside Flask. UI talks over REST +
Socket.IO (Flask polls/reads the unit’s SQLite or a small IPC), 1 s poll
fallback. Do not mix asyncio into the Flask worker.

## 3. Delta WS channels now vs docs

**Now (plugin):** public `v2/ticker`, `l2_orderbook` on
`wss://socket.india.delta.exchange`. Private after `key-auth`: `orders`,
`positions`, `margins`. No `user_trades`. Ticker quotes have bid/ask **price
only**. Local timestamp is `int(time.time()*1000)` on publish, not exchange ts.

**Docs (https://docs.delta.exchange, 14 Oct 2026 changelog):**

- Legacy `l1_orderbook` / `l2_orderbook` / `l2_updates` **fully deprecated 31 Oct 2026**.
- Public market data: `wss://public-socket.india.delta.exchange`.
- Replacement: `ob_l1` (best bid/ask **with sizes**), `ob_l2`, `ob_updates`, `ticker`.
- `ob_l1` fields: `bp`, `bs`, `ap`, `as`, `lts`, `ts`, `sy`. Publish ~100 ms.
- Option-chain subscribe: symbol `BTC-DDMMYY` (all calls+puts for that expiry).
- Private stays on `wss://socket.india.delta.exchange`. Docs list **UserTrades** / `v2/user_trades` in addition to Orders/Positions.
- IOC is a documented `time_in_force` on `POST /v2/orders`. Batch create does **not** support IOC — send legs as single-order POSTs.
- `client_order_id`: unique among **open** orders, **max 32 characters**.
- `GET /v2/orders/client_order_id/{client_oid}` exists (reconcile without retry).
- Margin preview: `GET /v2/products/{product_id}/margin_required` already wrapped in `broker/deltaexchange/api/margin_api.py`.

**Gap:** this plugin will be on deprecated public channels as of 31 Oct 2026.
The new unit must use `ob_l1` on the public socket from day one. Do not wait
for a plugin-wide migration (that is a separate change).

## 4. Products / expiries / strikes

- Strike Desk BTC CoA: public `GET /v2/tickers?contract_types=call_options,put_options&underlying_asset_symbols=BTC` in `delta_india.py`. Caches ATM window, `strike_step` 200. **No** `product_id`, commission, or `contract_value` on that path.
- Delta master contract: `broker/deltaexchange/database/master_contract_db.py`. `SymToken.token` = `product_id`, plus `tick_size`, `contract_value`. Loaded on broker login / master sync — **not** a 10-minute refresh, and **not running for Delta on GCE**.
- Products payload in docs includes `taker_commission_rate`. India **options fee-cap formula** is not spelled out in the same place-order page; Phase 2 must read the fees/products schema and implement from documented fields only (no hardcoded 0.0005).

## 5. Current auto-trade (btcalgo) vs here

**Here there is no auto-trade for this anomaly.** Nothing does
“far GTC @ ask → market fill gap → BUY near @ ask”.

If someone used OpenAlgo `placeorder` on this GCE host, the order would go to
**Zerodha**, not Delta. The new path must sign Delta REST with
`DELTA_INDIA_*` keys.

Existing `get_api_response` in `order_api.py` **retries POST on HTTP 429**.
That violates ground rule 5. New `delta_client.place_order` must not reuse
that helper for order POSTs. Signing (`get_auth_headers`) and
`get_httpx_client()` are reusable.

IOC is already mapped when OpenAlgo `validity == "IOC"`
(`transform_data.py`). Limit price is already a string.

## 6. Tests and how to run them

```bash
uv run pytest test/test_delta_india_credentials.py -q
cd strike_desk && uv run --group dev pytest tests/test_delta_india.py -q
```

No HMAC known-vector test exists yet (Phase 2 must add one against
`generate_signature`). No frontend/e2e for an anomalies panel. Frontend:
`cd frontend && npm test`.

## 7. Gaps vs spec, and proposed layout

**Must build (does not exist):** detection math, book store with sizes+age,
validate_opportunity, PairExecutor, risk/kill switch, audit SQLite, UI panel,
Telegram alerts, replay sim, systemd unit.

**Must not reuse as-is:** `place_order_api` (429 retry), Flask eventlet loop,
OpenAlgo `/api/v1/placeorder`, CoA BTC Hands webhook, legacy `l2_orderbook`.

**Reuse:** `get_auth_headers` / `generate_signature`, `get_httpx_client`,
`margin_api.calculate_margin_api`, CoA unit pattern, Flask + React tools page
+ Socket.IO, NullPool SQLite, telegram-desk if present (rate-limited).

### Proposed files

```
strike_desk/src/strike_desk/otm_anomaly/
  __main__.py              # systemd: python -m strike_desk.otm_anomaly
  settings.py              # Phase 1, SQLite-backed
  delta_client.py          # Phase 2, shared httpx, no POST retry
  books.py                 # Phase 3, ob_l1 + private WS
  detect.py                # existing math, unchanged
  validate.py              # Phase 4
  executor.py              # Phase 5
  risk.py                  # Phase 6
  audit.py                 # Phase 7, WAL + NullPool
  alerts.py                # Phase 9
strike_desk/scripts/strike-desk-otm.service
strike_desk/tests/test_otm_*.py
blueprints/otm_anomaly.py  # settings REST + poll
frontend/src/pages/OtmAnomaly.tsx
services/otm_anomaly_bridge.py   # Flask reads unit DB / heartbeat
```

UI route: `/otmanomaly` next to Lab (`/greekslab`). Profile/tools link later.
Default `execution_mode=dry_run`. `live` also requires `ALLOW_LIVE=1`.

Env (this ecosystem):

- `DELTA_INDIA_API_KEY` / `DELTA_INDIA_API_SECRET`
- `DELTA_BASE_URL` (already in `baseurl.py`)
- `DELTA_WS_URL` private, `DELTA_PUBLIC_WS_URL` for `ob_l1` (new)
- `ALLOW_LIVE`

GCE: new unit only. Do **not** restart `strike-desk.service`. Do not touch
QuadShield Live Auto. Do not share the NSE/BSE or BTC Hands Tradetron tokens.

## Docs vs spec (Phase 0 notes)

1. IOC is documented for single `POST /v2/orders`; not for batch create.
2. Public book channel for sizes is `ob_l1`, not `l1_orderbook`.
3. Two WS hosts: public vs private. Spec’s single `DELTA_WS_URL` is not enough.
4. `client_order_id` ≤ 32 chars, unique among open orders.
5. Options fee cap: **not found** on the place-order page; Phase 2 must locate
   the documented India options fee fields before coding `option_fee`.
