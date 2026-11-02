#!/usr/bin/env python3
"""Top up the open Natgas Analyze fill from 1 unit to 1 lot (1250)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from strike_desk.coa_analyzer import CoaAnalyzer
from strike_desk.config import get_settings
from strike_desk.openalgo_client import OpenAlgoClient

STATE = Path("/var/lib/strike-desk/coa_live_ng.json")
SYMBOL = "NATURALGAS20NOV26280CE"
TARGET = 1250
DBS = (
    Path("/opt/openalgo/current/db/openalgo.db"),
    Path("/opt/openalgo/db/openalgo.db"),
)


def _lotsize() -> int | None:
    for db in DBS:
        if not db.is_file():
            continue
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT lotsize FROM symtoken WHERE symbol = ? LIMIT 1",
                (SYMBOL,),
            ).fetchone()
        finally:
            con.close()
        if row:
            print("master_lotsize", row[0], "db", db)
            return int(row[0] or 0)
    print("master_lotsize unknown")
    return None


def main() -> None:
    lot = _lotsize()
    raw = json.loads(STATE.read_text(encoding="utf-8"))
    symbol = str(raw.get("analyzer_symbol") or "")
    qty = int(raw.get("analyzer_qty") or 0)
    if symbol != SYMBOL:
        raise SystemExit(f"unexpected analyzer_symbol {symbol!r}")
    if qty == TARGET:
        print("already", TARGET)
        return
    if qty != 1:
        raise SystemExit(f"unexpected analyzer_qty {qty}")
    settings = get_settings()
    client = OpenAlgoClient(settings)
    analyzer = CoaAnalyzer(settings, client)
    try:
        if lot and lot > 1 and TARGET % lot == 0:
            analyzer.place(SYMBOL, "SELL", qty, exchange="MCX")
            fill = analyzer.place(SYMBOL, "BUY", TARGET, exchange="MCX")
        else:
            fill = analyzer.place(SYMBOL, "BUY", TARGET - qty, exchange="MCX")
    finally:
        analyzer.close()
        client.close()
    raw["analyzer_qty"] = TARGET
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    tmp.replace(STATE)
    print("topup", fill.quantity, "orderid", fill.orderid, "now", TARGET)


if __name__ == "__main__":
    main()
