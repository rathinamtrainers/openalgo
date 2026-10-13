"""The /btc monitor reads the desk status file and the local arrest log."""

import json
import sqlite3
from pathlib import Path

from services.btc_desk_service import read_desk


def test_read_desk_reports_live_status_and_arrests(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BTC_DESK_ROOT", str(tmp_path))
    (tmp_path / "outputs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "outputs" / "otm_desk_status.json").write_text(
        json.dumps(
            {
                "mode": "paper",
                "spot": 83000.5,
                "expiries": 8,
                "contracts": 497,
                "fresh": 490,
                "lines": 1,
                "arrests": 1,
                "open_lines": [
                    {
                        "kind": "CE",
                        "expiry": "161026",
                        "near": "C-BTC-84000-161026",
                        "far": "C-BTC-86000-161026",
                        "near_ask": 20,
                        "far_bid": 40,
                        "diff": 20,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "data" / "delta.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE anomalies (
            id INTEGER PRIMARY KEY,
            detected_at REAL,
            kind TEXT,
            expiry TEXT,
            near_symbol TEXT,
            far_symbol TEXT,
            near_ask REAL,
            far_bid REAL,
            diff REAL,
            spot REAL,
            mode TEXT,
            qty INTEGER,
            detail TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO anomalies (
            detected_at, kind, expiry, near_symbol, far_symbol,
            near_ask, far_bid, diff, spot, mode, qty, detail
        ) VALUES (1, 'CE', '161026', 'C-BTC-84000-161026', 'C-BTC-86000-161026',
                  20, 40, 20, 83000, 'paper', 5, '{}')
        """
    )
    conn.commit()
    conn.close()

    payload = read_desk()

    assert payload["status"] == "success"
    data = payload["data"]
    assert data["alive"] is True
    assert data["desk"]["spot"] == 83000.5
    assert data["desk"]["open_lines"][0]["diff"] == 20
    assert data["arrests"][0]["mode"] == "paper"
    assert data["arrests"][0]["qty"] == 5
    assert data["positions_href"] == "/positions"
    assert data["orderbook_href"] == "/orderbook"


def test_read_desk_when_the_process_is_absent(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("BTC_DESK_ROOT", str(tmp_path))

    payload = read_desk()

    assert payload["status"] == "success"
    assert payload["data"]["alive"] is False
    assert payload["data"]["desk"] == {}
    assert payload["data"]["arrests"] == []
