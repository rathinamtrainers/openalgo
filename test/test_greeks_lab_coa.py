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
