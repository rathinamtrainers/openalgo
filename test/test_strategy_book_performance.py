import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.strategy_book_performance import (
    book_performance,
    sandbox_account_performance,
    sandbox_strategy_report,
    trade_belongs_to_book,
)
from services.strategy_timeline_service import list_timeline


def _analyzer_book():
    return {
        "id": "analyzer-nifty",
        "kind": "analyzer",
        "underlying": "NIFTY",
        "execution_target": "openalgo",
        "name": "Strike Desk NIFTY Analyzer",
    }


def _qs_book():
    return {
        "id": "qs-nifty",
        "kind": "strikeshield",
        "execution_target": "tradetron-hands",
        "name": "QuadShield",
    }


def _trade(**overrides):
    row = {
        "trade_id": "t1",
        "created_at_utc": "2026-10-21T04:53:20+00:00",
        "created_at_ist": "2026-10-21 10:23:20 IST",
        "trading_day": "2026-10-21",
        "symbol": "NIFTY27OCT2622950PE",
        "exchange": "NFO",
        "product": "NRML",
        "action": "BUY",
        "quantity": 75,
        "price": 163.1,
        "strategy": "strike-desk:4a74469e",
    }
    row.update(overrides)
    return row


def test_analyzer_tag_belongs_and_ofa_does_not():
    book = _analyzer_book()
    assert trade_belongs_to_book(book, "strike-desk:4a74469e")
    assert not trade_belongs_to_book(book, "OFA")
    assert not trade_belongs_to_book(_qs_book(), "strike-desk:4a74469e")


def test_round_trip_realized_pnl_and_win():
    trades = [
        _trade(),
        _trade(
            trade_id="t2",
            created_at_utc="2026-10-21T06:10:07+00:00",
            created_at_ist="2026-10-21 11:40:07 IST",
            action="SELL",
            price=150.0,
            strategy="AUTO_SQUARE_OFF",
        ),
    ]
    payload = book_performance(_analyzer_book(), trades=trades, positions=[])
    summary = payload["summary"]
    assert summary["fills"] == 2
    assert summary["buys"] == 1
    assert summary["sells"] == 1
    assert summary["closed_round_trips"] == 1
    assert summary["losses"] == 1
    assert summary["wins"] == 0
    assert summary["realized_pnl"] == -982.5
    assert summary["open_quantity"] == 0
    assert payload["round_trips"][0]["pnl"] == -982.5
    assert payload["round_trips"][0]["roi_pct"] == -8.03
    assert payload["round_trips"][0]["capital"] == 12232.5
    assert payload["source"] == "openalgo-analyze"
    assert summary["roi_pct"] == -8.03
    assert summary["capital_deployed"] == 12232.5
    assert summary["expectancy"] == -982.5
    assert summary["calendar_days"] == 1
    assert summary["trading_days"] == 1
    assert summary["avg_loss_day"] == -982.5
    assert summary["cagr_potential_pct"] is None


def test_open_position_uses_ltp_for_unrealized():
    trades = [_trade()]
    positions = [
        {
            "symbol": "NIFTY27OCT2622950PE",
            "exchange": "NFO",
            "product": "NRML",
            "quantity": 75,
            "average_price": 163.1,
            "ltp": 170.1,
            "unrealized_pnl": 525.0,
            "accumulated_realized_pnl": 0.0,
        }
    ]
    payload = book_performance(_analyzer_book(), trades=trades, positions=positions)
    summary = payload["summary"]
    assert summary["realized_pnl"] == 0.0
    assert summary["unrealized_pnl"] == 525.0
    assert summary["open_quantity"] == 75
    assert payload["open_positions"][0]["symbol"] == "NIFTY27OCT2622950PE"


def test_day_filter_excludes_other_sessions():
    trades = [
        _trade(),
        _trade(
            trade_id="t-old",
            created_at_utc="2026-10-20T04:53:20+00:00",
            created_at_ist="2026-10-20 10:23:20 IST",
            trading_day="2026-10-20",
            price=100.0,
        ),
    ]
    payload = book_performance(_analyzer_book(), trades=trades, positions=[], day="2026-10-21")
    assert payload["summary"]["fills"] == 1
    assert payload["day"] == "2026-10-21"


def test_hands_book_has_empty_pnl_and_keeps_decision_counts():
    events = [
        {"kind": "decision", "outcome": "hold", "reason_code": "wings-shorts"},
        {"kind": "status", "outcome": "hold", "reason_code": "wings-open"},
    ]
    payload = book_performance(
        _qs_book(),
        events,
        trades=[_trade()],
        positions=[],
    )
    assert payload["source"] == "openalgo-analyze"
    assert payload["summary"]["fills"] == 0
    assert payload["summary"]["hold_decisions"] == 2
    assert payload["trades"] == []


def test_webhook_and_future_book_match_their_own_tags():
    webhook = {"id": 44, "name": "TV Nifty Bot"}
    future = {"id": "coa-nifty", "name": "Strike Desk CoA NIFTY", "kind": "overlay"}
    trades = [
        _trade(strategy="TV Nifty Bot", price=10.0),
        _trade(
            trade_id="t2",
            created_at_utc="2026-10-21T05:00:00+00:00",
            action="SELL",
            price=12.0,
            strategy="TV Nifty Bot",
        ),
        _trade(
            trade_id="t3",
            created_at_utc="2026-10-21T05:10:00+00:00",
            strategy="coa-nifty:tick",
            price=20.0,
        ),
    ]
    webhook_pnl = book_performance(webhook, trades=trades, positions=[])
    assert webhook_pnl["summary"]["fills"] == 2
    assert webhook_pnl["summary"]["realized_pnl"] == 150.0
    future_pnl = book_performance(future, trades=trades, positions=[])
    assert future_pnl["summary"]["fills"] == 1
    assert future_pnl["summary"]["open_quantity"] == 75
    tagged = book_performance(
        {"id": "coa-nifty", "name": "Strike Desk CoA NIFTY"},
        trades=[_trade(strategy="COA", price=20.0)],
        positions=[],
    )
    assert tagged["summary"]["fills"] == 1
    assert tagged["summary"]["open_quantity"] == 75
    assert book_performance(_qs_book(), trades=trades, positions=[])["summary"]["fills"] == 0


def test_two_day_curve_sets_sharpe_and_sortino():
    trades = [
        _trade(price=100.0),
        _trade(
            trade_id="t2",
            created_at_utc="2026-10-21T06:00:00+00:00",
            created_at_ist="2026-10-21 11:30:00 IST",
            action="SELL",
            price=120.0,
            strategy="AUTO_SQUARE_OFF",
        ),
        _trade(
            trade_id="t3",
            created_at_utc="2026-10-22T04:53:20+00:00",
            created_at_ist="2026-10-22 10:23:20 IST",
            trading_day="2026-10-22",
            price=100.0,
        ),
        _trade(
            trade_id="t4",
            created_at_utc="2026-10-22T06:00:00+00:00",
            created_at_ist="2026-10-22 11:30:00 IST",
            trading_day="2026-10-22",
            action="SELL",
            price=90.0,
            strategy="AUTO_SQUARE_OFF",
        ),
    ]
    summary = book_performance(_analyzer_book(), trades=trades, positions=[])["summary"]
    assert summary["closed_round_trips"] == 2
    assert summary["roi_pct"] is not None
    assert summary["sharpe"] is not None
    assert summary["sortino"] is not None
    assert summary["calendar_days"] == 2
    assert summary["trading_days"] == 2
    assert summary["avg_profit_day"] == 1500.0
    assert summary["avg_loss_day"] == -750.0
    assert summary["cagr_potential_pct"] is None
    assert summary["max_drawdown_pct"] is not None


def test_ofa_trade_is_not_attributed_to_analyzer():
    trades = [
        _trade(strategy="OFA", action="BUY", price=80.0),
        _trade(
            trade_id="t2",
            created_at_utc="2026-10-21T05:00:00+00:00",
            action="SELL",
            price=90.0,
            strategy="OFA",
        ),
    ]
    payload = book_performance(_analyzer_book(), trades=trades, positions=[])
    assert payload["summary"]["fills"] == 0


def test_timeline_includes_performance_block(monkeypatch):
    monkeypatch.setattr(
        "services.strategy_timeline_service.get_book", lambda _book_id: _analyzer_book()
    )
    monkeypatch.setattr(
        "services.strategy_timeline_service.list_decisions",
        lambda **_kwargs: {"decisions": []},
    )
    monkeypatch.setattr(
        "services.strategy_timeline_service._analyzer_fill_events", lambda _limit: []
    )
    monkeypatch.setattr(
        "services.strategy_book_performance._load_sandbox_rows",
        lambda: (
            [
                _trade(),
                _trade(
                    trade_id="t2",
                    created_at_utc="2026-10-21T06:10:07+00:00",
                    created_at_ist="2026-10-21 11:40:07 IST",
                    action="SELL",
                    price=150.0,
                    strategy="AUTO_SQUARE_OFF",
                ),
            ],
            [],
        ),
    )
    payload = list_timeline("analyzer-nifty")
    assert payload["performance"]["summary"]["realized_pnl"] == -982.5
    assert payload["performance"]["summary"]["fills"] == 2


def test_sandbox_account_performance_includes_every_fill(monkeypatch):
    trades = [
        _trade(),
        _trade(
            trade_id="t2",
            created_at_utc="2026-10-21T06:10:07+00:00",
            created_at_ist="2026-10-21 11:40:07 IST",
            action="SELL",
            price=150.0,
            strategy="AUTO_SQUARE_OFF",
        ),
        _trade(
            trade_id="t3",
            created_at_utc="2026-10-22T04:00:00+00:00",
            created_at_ist="2026-10-22 09:30:00 IST",
            trading_day="2026-10-22",
            symbol="NIFTY27OCT2622500PE",
            strategy="OFA",
            price="80",
            quantity=65,
        ),
    ]
    monkeypatch.setattr(
        "services.strategy_book_performance._load_sandbox_rows",
        lambda: (trades, []),
    )
    payload = sandbox_account_performance()
    assert payload["summary"]["fills"] == 3
    assert payload["summary"]["closed_round_trips"] == 1
    assert payload["round_trips"][0]["roi_pct"] == -8.03
    assert "every ZeroTouchAlgo Analyze fill" in payload["source_note"]


def test_strategy_report_splits_coa_from_analyzer_and_summarizes_trips(monkeypatch):
    trades = [
        _trade(strategy="COA", price=160.2, quantity=65),
        _trade(
            trade_id="t2",
            created_at_utc="2026-10-21T06:10:07+00:00",
            created_at_ist="2026-10-21 11:40:07 IST",
            action="SELL",
            price=184.4,
            quantity=65,
            strategy="COA",
        ),
        _trade(
            trade_id="t3",
            created_at_utc="2026-10-19T06:08:20+00:00",
            created_at_ist="2026-10-19 11:38:20 IST",
            trading_day="2026-10-19",
            strategy="strike-desk:b6adcad4",
            price=100.0,
        ),
        _trade(
            trade_id="t4",
            created_at_utc="2026-10-19T08:14:20+00:00",
            created_at_ist="2026-10-19 13:44:20 IST",
            trading_day="2026-10-19",
            action="SELL",
            price=90.0,
            strategy="AUTO_SQUARE_OFF",
        ),
    ]
    monkeypatch.setattr(
        "services.strategy_book_performance._load_sandbox_rows",
        lambda: (trades, []),
    )
    monkeypatch.setattr(
        "database.strategy_books_db.list_books",
        lambda: [
            _analyzer_book(),
            {"id": "coa-nifty", "name": "Strike Desk CoA NIFTY", "kind": "coa"},
        ],
    )
    report = sandbox_strategy_report()
    by_id = {row["id"]: row for row in report["strategies"]}
    assert by_id["coa-nifty"]["performance"]["summary"]["fills"] == 2
    assert by_id["coa-nifty"]["performance"]["summary"]["closed_round_trips"] == 1
    assert by_id["coa-nifty"]["performance"]["summary"]["realized_pnl"] == round(
        (184.4 - 160.2) * 65, 2
    )
    assert by_id["analyzer-nifty"]["performance"]["summary"]["fills"] == 2
    assert by_id["analyzer-nifty"]["performance"]["summary"]["realized_pnl"] == -750.0
    assert report["overall"]["summary"]["fills"] == 4
    closed = [row for row in report["trade_summaries"] if row["status"] == "closed"]
    assert len(closed) == 2
    names = {row["strategy_name"] for row in closed}
    assert names == {"Strike Desk CoA NIFTY", "Strike Desk NIFTY Analyzer"}
    coa_trip = next(row for row in closed if row["strategy_id"] == "coa-nifty")
    assert coa_trip["symbol"] == "NIFTY27OCT2622950PE"
    assert coa_trip["entry_price"] == 160.2
    assert coa_trip["exit_price"] == 184.4


def test_strategy_report_date_window_excludes_other_days(monkeypatch):
    trades = [
        _trade(strategy="COA", price=100.0),
        _trade(
            trade_id="t2",
            created_at_utc="2026-10-22T04:53:20+00:00",
            created_at_ist="2026-10-22 10:23:20 IST",
            trading_day="2026-10-22",
            strategy="COA",
            price=80.0,
        ),
    ]
    monkeypatch.setattr(
        "services.strategy_book_performance._load_sandbox_rows",
        lambda: (trades, []),
    )
    monkeypatch.setattr(
        "database.strategy_books_db.list_books",
        lambda: [{"id": "coa-nifty", "name": "Strike Desk CoA NIFTY", "kind": "coa"}],
    )
    report = sandbox_strategy_report(start_date="2026-10-22", end_date="2026-10-22")
    coa = report["strategies"][0]
    assert coa["performance"]["summary"]["fills"] == 1
    assert coa["performance"]["day"] == "2026-10-22…2026-10-22"


def test_same_day_gain_does_not_invent_trillion_yearly_pace():
    trades = [
        _trade(price=100.0),
        _trade(
            trade_id="t2",
            created_at_utc="2026-10-21T06:00:00+00:00",
            created_at_ist="2026-10-21 11:30:00 IST",
            action="SELL",
            price=107.0,
            strategy="AUTO_SQUARE_OFF",
        ),
    ]
    summary = book_performance(_analyzer_book(), trades=trades, positions=[])["summary"]
    assert summary["realized_pnl"] == 525.0
    assert summary["roi_pct"] == 7.0
    assert summary["calendar_days"] == 1
    assert summary["avg_profit_day"] == 525.0
    assert summary["cagr_potential_pct"] is None
    assert summary["cagr_pct"] is None


def test_yearly_pace_is_linear_after_three_weeks():
    trades = [
        _trade(
            price=100.0,
            created_at_utc="2026-09-01T04:53:20+00:00",
            created_at_ist="2026-09-01 10:23:20 IST",
            trading_day="2026-09-01",
        ),
        _trade(
            trade_id="t2",
            created_at_utc="2026-09-22T06:00:00+00:00",
            created_at_ist="2026-09-22 11:30:00 IST",
            trading_day="2026-09-22",
            action="SELL",
            price=107.0,
            strategy="AUTO_SQUARE_OFF",
        ),
    ]
    summary = book_performance(_analyzer_book(), trades=trades, positions=[])["summary"]
    assert summary["calendar_days"] == 22
    assert summary["trading_days"] == 16
    expected = round(7.0 * 365.25 / 22, 2)
    assert summary["cagr_potential_pct"] == expected
    assert expected < 500
