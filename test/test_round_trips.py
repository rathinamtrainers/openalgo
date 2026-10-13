"""FIFO round-trip blotter used by sandbox Positions and analyzer logs."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sandbox.round_trips import (
    attach_running_totals,
    attach_trips_to_requests,
    blotter_fields_for_position,
    drop_entry_rows_when_closed,
    last_trip_by_leg,
    pair_round_trips,
    parse_option_symbol,
    serialize_trip,
    summarize_round_trips,
)


def _fill(symbol, action, qty, price, minute, second=0, exchange="NFO", product="MIS"):
    return {
        "symbol": symbol,
        "exchange": exchange,
        "product": product,
        "action": action,
        "quantity": qty,
        "price": price,
        "timestamp": datetime(2026, 10, 5, 10 if action == "BUY" else 12, minute, second),
        "tradeid": f"{action}-{minute}-{second}-{qty}",
    }


def test_parse_nifty_weekly_put():
    parsed = parse_option_symbol("NIFTY13OCT2622500PE")
    assert parsed["underlying"] == "NIFTY"
    assert parsed["expiry"] == "13OCT26"
    assert parsed["strike"] == "22500"
    assert parsed["option_type"] == "PE"


def test_today_long_put_round_trip():
    fills = [
        _fill("NIFTY13OCT2622500PE", "BUY", 65, 160.10, 30, 37),
        _fill("NIFTY13OCT2622500PE", "SELL", 65, 223.30, 8, 14),
    ]
    fills[1]["timestamp"] = datetime(2026, 10, 5, 12, 8, 14)
    trips = pair_round_trips(fills)
    assert len(trips) == 1
    trip = trips[0]
    assert trip["open"] is False
    assert trip["entry_price"] == 160.10
    assert trip["exit_price"] == 223.30
    assert trip["entry_time"] == "2026-10-05 10:30:37"
    assert trip["exit_time"] == "2026-10-05 12:08:14"
    assert trip["roi_percent"] == 39.48
    assert trip["strike"] == "22500"
    assert trip["option_type"] == "PE"

    fields = blotter_fields_for_position(trip, today_realized_pnl=4108.0)
    assert fields["traded_quantity"] == 65
    assert fields["roi_percent"] == 39.48
    rupee = (223.30 - 160.10) * 65
    assert round(rupee, 2) == 4108.00


def test_open_long_has_no_exit():
    trips = pair_round_trips([_fill("NIFTY13OCT2622500PE", "BUY", 65, 160.10, 30, 37)])
    assert trips[0]["open"] is True
    assert trips[0]["exit_time"] is None
    fields = blotter_fields_for_position(trips[0], ltp=180.0)
    assert fields["exit_price"] is None
    assert fields["roi_percent"] == 12.43


def test_last_trip_prefers_open_over_earlier_close():
    fills = [
        _fill("NIFTY13OCT2622450PE", "BUY", 65, 100, 0, 0),
        _fill("NIFTY13OCT2622450PE", "SELL", 65, 90, 15, 0),
        _fill("NIFTY13OCT2622450PE", "BUY", 65, 80, 45, 0),
    ]
    fills[1]["timestamp"] = datetime(2026, 10, 5, 11, 15, 0)
    fills[2]["timestamp"] = datetime(2026, 10, 5, 13, 45, 0)
    last = last_trip_by_leg(pair_round_trips(fills))
    trip = last[("NIFTY13OCT2622450PE", "NFO", "MIS")]
    assert trip["open"] is True
    assert trip["entry_price"] == 80.0


def test_attach_trips_to_placeorder_and_closeposition():
    fills = [
        _fill("NIFTY13OCT2622500PE", "BUY", 65, 160.10, 30, 37),
        _fill("NIFTY13OCT2622500PE", "SELL", 65, 223.30, 8, 14),
    ]
    fills[1]["timestamp"] = datetime(2026, 10, 5, 12, 8, 14)
    trips = pair_round_trips(fills)
    requests = [
        {
            "timestamp": "2026-10-05 10:30:37",
            "api_type": "placeorder",
            "symbol": "NIFTY13OCT2622500PE",
        },
        {
            "timestamp": "2026-10-05 12:08:14",
            "api_type": "closeposition",
            "symbol": "",
        },
    ]
    attach_trips_to_requests(requests, trips)
    buy, close = requests
    assert buy["strike"] == "22500"
    assert buy["entry_price"] == 160.10
    assert buy.get("trade_pnl") is None
    assert buy.get("exit_price") is None
    assert buy.get("roi_percent") is None
    assert close["symbol"] == "NIFTY13OCT2622500PE"
    assert close["exit_price"] == 223.30
    assert close["trade_pnl"] == 4108.0
    assert close["roi_percent"] == 39.48
    visible = drop_entry_rows_when_closed(requests)
    assert len(visible) == 1
    assert visible[0]["api_type"] == "closeposition"
    assert serialize_trip(trips[0])["trade_pnl"] == 4108.0


def test_open_placeorder_stays_until_close():
    trips = pair_round_trips([_fill("NIFTY13OCT2622500PE", "BUY", 65, 160.10, 30, 37)])
    requests = [
        {
            "timestamp": "2026-10-05 10:30:37",
            "api_type": "placeorder",
            "symbol": "NIFTY13OCT2622500PE",
        }
    ]
    attach_trips_to_requests(requests, trips)
    visible = drop_entry_rows_when_closed(requests)
    assert len(visible) == 1
    assert visible[0]["api_type"] == "placeorder"


def _closed_trip(symbol, entry, exit_px, qty=65):
    buy = _fill(symbol, "BUY", qty, entry, 30, 0)
    sell = _fill(symbol, "SELL", qty, exit_px, 8, 0)
    sell["timestamp"] = datetime(2026, 10, 5, 12, 8, 0)
    return serialize_trip(pair_round_trips([buy, sell])[0])


def test_summarize_round_trips_tt_style_book():
    winner = _closed_trip("NIFTY13OCT2622500PE", 160.10, 223.30)
    loser = _closed_trip("NIFTY13OCT2622550PE", 144.65, 114.65)
    open_trip = serialize_trip(
        pair_round_trips([_fill("NIFTY19OCT2622400PE", "BUY", 65, 168.40, 0, 24)])[0]
    )
    stats = summarize_round_trips([winner, loser, open_trip])
    assert winner["trade_pnl"] == 4108.0
    assert loser["trade_pnl"] == -1950.0
    assert stats["trades"] == 2
    assert stats["open_trades"] == 1
    assert stats["net_pnl"] == 2158.0
    assert stats["winning"] == 1
    assert stats["losing"] == 1
    assert stats["win_rate"] == 50.0
    assert stats["gross_profit"] == 4108.0
    assert stats["gross_loss"] == 1950.0
    assert stats["profit_factor"] == 2.11
    assert stats["expectancy"] == 1079.0
    assert stats["best_trade"] == 4108.0
    assert stats["worst_trade"] == -1950.0
    assert stats["max_consecutive_wins"] == 1
    assert stats["max_consecutive_losses"] == 1
    assert stats["max_drawdown"] == 1950.0
    assert stats["max_drawdown_pct"] == 9.84
    assert stats["pe_trades"] == 2
    assert stats["ce_trades"] == 0
    assert stats["total_premium"] == 19808.75
    assert stats["roi_percent"] == 10.89
    assert stats["avg_roi_percent"] == stats["roi_percent"]
    assert "sharpe" in stats["withheld"]


def test_running_cum_roi_matches_headline():
    winner = _closed_trip("NIFTY13OCT2622500PE", 160.10, 223.30)
    loser = _closed_trip("NIFTY13OCT2622550PE", 144.65, 114.65)
    winner["exit_time"] = "2026-10-05 12:00:00"
    loser["exit_time"] = "2026-10-05 13:00:00"
    rows = attach_running_totals([winner, loser])
    stats = summarize_round_trips(rows)
    assert rows[0]["cum_pnl"] == 4108.0
    assert rows[1]["cum_pnl"] == stats["net_pnl"] == 2158.0
    assert rows[1]["cum_roi_percent"] == stats["roi_percent"] == 10.89


def test_summarize_empty_book():
    stats = summarize_round_trips([])
    assert stats["trades"] == 0
    assert stats["net_pnl"] == 0.0
    assert stats["roi_percent"] is None
    assert stats["profit_factor"] is None
