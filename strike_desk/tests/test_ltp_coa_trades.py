from datetime import datetime, timedelta

from strike_desk.coa_boards import BANKNIFTY, NIFTY
from strike_desk.config import IST
from strike_desk.ltp_coa_exits import OpenTrade
from strike_desk.ltp_coa_trades import (
    hold_label,
    load_trades,
    mark_money,
    peak_pct,
    record_entry,
    record_exit,
    record_mark,
    strike_label,
)


def _trade(**kwargs) -> OpenTrade:
    base = dict(
        side="CE",
        entry=877.5,
        entry_ts=datetime(2026, 10, 30, 11, 56, 22, tzinfo=IST),
        peak=877.5,
        eos=54800.0,
        eor=55200.0,
        support=54840.0,
        resistance=55160.0,
        scenario="1.1",
        strike=55000.0,
    )
    base.update(kwargs)
    return OpenTrade(**base)


def test_hold_label_matches_the_summary_table():
    assert hold_label(20) == "<1 min"
    assert hold_label(188) == "~3 min"
    assert hold_label(247) == "~4 min"


def test_entry_then_exit_last_line_wins(tmp_path):
    trade = _trade()
    record_entry(tmp_path, BANKNIFTY, trade)
    trade.peak = 940.0
    record_exit(
        tmp_path,
        BANKNIFTY,
        trade,
        now=datetime(2026, 10, 30, 11, 59, 30, tzinfo=IST),
        exit_ltp=932.9,
        reason="spot target opposite S/R",
        exit_scenario="1.1",
        lot_size=15,
        lots=1,
        qty=15,
        expiry="28 Oct 2026",
        symbol="BANKNIFTY28OCT2655000CE",
    )
    rows = load_trades(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Banknifty"
    assert row["open"] is False
    assert row["in_label"] == "CE on CoA 1.1"
    assert row["out_label"] == "spot target opposite S/R"
    assert row["hold_label"] == "~3 min"
    assert row["ist_span"] == "11:56–11:59"
    assert row["entry_ltp"] == 877.5
    assert row["exit_ltp"] == 932.9
    assert row["strike"] == 55000.0
    assert row["strike_label"] == "55000 CE · 28 Oct 2026"
    assert row["lot_size"] == 15
    assert row["lots"] == 1
    assert row["qty"] == 15
    assert row["peak_ltp"] == 940.0
    assert row["peak_pct"] == 7.1
    assert row["atm_pnl"] == 831.0
    assert row["mfe"] == 937.5
    assert row["margin_used"] == 13162.5
    assert row["currency"] == "INR"
    assert "token" not in row and "sid" not in row


def test_scenario_kill_shortens_the_out_label(tmp_path):
    trade = _trade(side="PE", scenario="1.6", entry=607.1)
    trade.entry_ts = datetime(2026, 10, 30, 9, 35, 5, tzinfo=IST)
    record_exit(
        tmp_path,
        NIFTY,
        trade,
        now=datetime(2026, 10, 30, 9, 39, 12, tzinfo=IST),
        exit_ltp=628.1,
        reason="scenario kill CoA 1.9",
        exit_scenario="1.9",
    )
    row = load_trades(tmp_path)[0]
    assert row["in_label"] == "PE on CoA 1.6"
    assert row["out_label"] == "scenario kill 1.9"
    assert row["rule"] == "scenario kill CoA 1.9"


def test_open_row_hold_clock_updates(tmp_path):
    trade = _trade()
    record_entry(tmp_path, BANKNIFTY, trade)
    later = trade.entry_ts + timedelta(minutes=8)
    row = load_trades(tmp_path, now=later)[0]
    assert row["open"] is True
    assert row["out_label"] == "open"
    assert row["hold_label"] == "~8 min"
    assert row["atm_pnl"] is None


def test_new_high_ratchets_watermark_while_open(tmp_path):
    trade = _trade()
    record_entry(tmp_path, BANKNIFTY, trade, lot_size=30, lots=1, qty=30)
    trade.peak = 940.0
    record_mark(
        tmp_path,
        BANKNIFTY,
        trade,
        ltp=910.0,
        lot_size=30,
        lots=1,
        qty=30,
    )
    row = load_trades(tmp_path)[0]
    assert row["open"] is True
    assert row["peak_ltp"] == 940.0
    assert row["peak_pct"] == 7.1
    assert row["mark_ltp"] == 910.0
    assert row["mfe"] == 1875.0
    assert row["atm_pnl"] == 975.0
    assert row["margin_used"] == 26325.0


def test_strike_and_mark_helpers():
    assert strike_label("CE", 55000, "28 Oct 2026") == "55000 CE · 28 Oct 2026"
    assert strike_label("PE", None) == "PE"
    assert peak_pct(877.5, 940.0) == 7.1
    assert mark_money(877.5, 932.9, 15) == 831.0
    assert mark_money(877.5, 932.9, None) is None
