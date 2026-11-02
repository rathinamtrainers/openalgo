"""Lab CoA board reads public briefs only."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import greeks_lab_coa as coa


def test_coa_boards_from_local_files(tmp_path, monkeypatch):
    monkeypatch.setattr(coa, "BRIEF_DIR", Path(tmp_path))
    monkeypatch.setattr(coa, "_read_http", lambda _name: None)
    (tmp_path / "public-brief.json").write_text(
        json.dumps(
            {
                "market": "nifty",
                "scenario_label": "WATCHING",
                "last": 23093.5,
                "do_now": "Do nothing.",
                "secret": "nope",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "public-brief-banknifty.json").write_text(
        json.dumps({"market": "banknifty", "scenario_label": "CALL TAGGED", "last": 54958.0}),
        encoding="utf-8",
    )
    out = coa.get_coa_boards()
    assert out["status"] == "success"
    assert [row["slug"] for row in out["boards"]] == [
        "nifty",
        "banknifty",
        "sensex",
        "crudeoil",
        "natgas",
        "btc-options",
    ]
    by_slug = {row["slug"]: row for row in out["boards"]}
    assert by_slug["nifty"]["scenario_label"] == "WATCHING"
    assert by_slug["nifty"]["last"] == 23093.5
    assert by_slug["nifty"]["source"] == "coa"
    assert "secret" not in by_slug["nifty"]
    assert by_slug["banknifty"]["scenario_label"] == "CALL TAGGED"
    assert by_slug["banknifty"]["greeks_underlying"] == "BANKNIFTY"
    assert by_slug["sensex"]["missing"] is True
    assert by_slug["crudeoil"]["missing"] is True
    assert by_slug["crudeoil"]["greeks_exchange"] == "MCX"
    assert by_slug["crudeoil"]["greeks_underlying"] == "CRUDEOIL"
    assert by_slug["natgas"]["greeks_underlying"] == "NATURALGAS"
    assert by_slug["btc-options"]["missing"] is True
    assert by_slug["btc-options"]["greeks_exchange"] is None
    assert by_slug["btc-options"]["greeks_underlying"] is None


def test_crude_and_natgas_read_coa_briefs(tmp_path, monkeypatch):
    monkeypatch.setattr(coa, "BRIEF_DIR", Path(tmp_path))
    monkeypatch.setattr(coa, "_read_http", lambda _name: None)
    (tmp_path / "public-brief-crudeoil.json").write_text(
        json.dumps({"market": "crudeoil", "scenario_label": "CALL TAGGED", "last": 5432.5}),
        encoding="utf-8",
    )
    (tmp_path / "public-brief-natgas.json").write_text(
        json.dumps({"market": "natgas", "scenario_label": "WATCHING", "last": 279.2}),
        encoding="utf-8",
    )
    out = coa.get_coa_boards()
    by_slug = {row["slug"]: row for row in out["boards"]}
    assert by_slug["crudeoil"]["source"] == "coa"
    assert by_slug["crudeoil"]["scenario_label"] == "CALL TAGGED"
    assert by_slug["crudeoil"]["last"] == 5432.5
    assert by_slug["crudeoil"]["greeks_exchange"] == "MCX"
    assert by_slug["natgas"]["scenario_label"] == "WATCHING"
    assert by_slug["natgas"]["greeks_underlying"] == "NATURALGAS"


def test_btc_brief_keeps_levels_and_has_no_openalgo_greeks(tmp_path, monkeypatch):
    monkeypatch.setattr(coa, "BRIEF_DIR", Path(tmp_path))
    monkeypatch.setattr(coa, "_read_http", lambda _name: None)
    (tmp_path / "public-brief-btc-options.json").write_text(
        json.dumps(
            {
                "market": "btc-options",
                "scenario_label": "WATCHING",
                "last": 77207.3,
                "mood": "the map is mixed",
                "support": 77000,
                "eos": 76920,
                "resistance": 79000,
                "eor": 79080,
                "disclaimer": "Map, not advice.",
            }
        ),
        encoding="utf-8",
    )
    out = coa.get_coa_boards()
    btc = {row["slug"]: row for row in out["boards"]}["btc-options"]
    assert btc["greeks_exchange"] is None
    assert btc["greeks_underlying"] is None
    assert btc["last"] == 77207.3
    assert btc["mood"] == "the map is mixed"
    assert btc["eos"] == 76920
    assert btc["disclaimer"] == "Map, not advice."


def test_coa_trades_last_line_wins_and_strips_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(coa, "BRIEF_DIR", Path(tmp_path))
    (tmp_path / "coa-trades.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "banknifty:1",
                        "board": "banknifty",
                        "title": "Banknifty",
                        "in_label": "CE on CoA 1.1",
                        "open": True,
                        "token": "nope",
                    }
                ),
                json.dumps(
                    {
                        "id": "banknifty:1",
                        "board": "banknifty",
                        "title": "Banknifty",
                        "in_label": "CE on CoA 1.1",
                        "out_label": "spot target opposite S/R",
                        "rule": "spot target opposite S/R",
                        "hold_label": "~3 min",
                        "open": False,
                        "lot_size": 15,
                        "qty": 15,
                        "atm_pnl": 831.0,
                        "peak_ltp": 940.0,
                        "token": "nope",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = coa.get_coa_trades()
    assert out["status"] == "success"
    assert out["count"] == 1
    row = out["trades"][0]
    assert row["out_label"] == "spot target opposite S/R"
    assert row["open"] is False
    assert row["atm_pnl"] == 831.0
    assert row["lot_size"] == 15
    assert "token" not in row


def test_coa_trades_overlays_live_peak(tmp_path, monkeypatch):
    monkeypatch.setattr(coa, "BRIEF_DIR", Path(tmp_path))
    (tmp_path / "coa-trades.jsonl").write_text(
        json.dumps(
            {
                "id": "nifty:1",
                "board": "nifty",
                "title": "Nifty",
                "in_at": "2026-10-30T11:00:00+05:30",
                "in_ist": "11:00",
                "in_label": "CE on CoA 1.1",
                "open": True,
                "entry_ltp": 120,
                "qty": 65,
                "currency": "INR",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "coa_live.json").write_text(
        json.dumps({"open": {"peak": 150, "last_ltp": 140, "side": "CE"}}),
        encoding="utf-8",
    )
    row = coa.get_coa_trades()["trades"][0]
    assert row["peak_ltp"] == 150.0
    assert row["peak_pct"] == 25.0
    assert row["mark_ltp"] == 140.0
    assert row["atm_pnl"] == 1300.0
    assert row["mfe"] == 1950.0
    assert row["margin_used"] == 7800.0


def test_coa_trades_empty_file_is_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(coa, "BRIEF_DIR", Path(tmp_path))
    out = coa.get_coa_trades()
    assert out["trades"] == []
    assert out["count"] == 0
