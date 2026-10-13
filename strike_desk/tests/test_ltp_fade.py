"""NIFTY LTP 9:20 Fade — §9 arithmetic, fail-closed freeze, paper path."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from strike_desk.ltp_fade import (
    REVERSAL_MODE,
    ChainSnapshot,
    FrozenLines,
    LtpFadeEngine,
    PaperFade,
    SessionState,
    StrikeRow,
    call_reversal,
    evaluate_entry,
    evaluate_exit,
    freeze_lines,
    put_reversal,
    snapshot_from_chain,
    walk_resistance,
    walk_support,
)
from strike_desk.ltp_fade.walk import atm_spot

IST = ZoneInfo("Asia/Kolkata")
LOT = 75


def _now(hour: int, minute: int, day: int = 30) -> datetime:
    return datetime(2026, 4, day, hour, minute, tzinfo=IST)


def _row(
    strike: int,
    *,
    ce_ltp: float | None = 20.0,
    pe_ltp: float | None = 20.0,
    ce_volume: float | None = 100.0,
    pe_volume: float | None = 100.0,
    lot_size: int = LOT,
) -> StrikeRow:
    return StrikeRow(
        strike=strike,
        ce_ltp=ce_ltp,
        pe_ltp=pe_ltp,
        ce_volume=ce_volume,
        pe_volume=pe_volume,
        ce_symbol=f"NIFTY30APR26{strike}CE",
        pe_symbol=f"NIFTY30APR26{strike}PE",
        lot_size=lot_size,
    )


def _snapshot(spot: float, rows: list[StrikeRow], as_of: datetime | None = None) -> ChainSnapshot:
    when = as_of or _now(9, 21)
    return ChainSnapshot(spot=spot, expiry="2026-04-30", rows=tuple(rows), as_of=when)


# --- §9 reversal proxy -------------------------------------------------------


def test_call_and_put_reversal_proxy():
    assert call_reversal(19300, 24.9) == 19324.9
    assert put_reversal(19300, 24.0) == 19276.0
    assert call_reversal(24300, 35.5) == 24335.5
    assert put_reversal(24300, 2.0) == 24298.0
    assert REVERSAL_MODE == "proxy_k_plus_minus_ltp"


def test_stops_from_published_graphic():
    eor2 = 23116.0
    eos2 = 22861.0
    assert eor2 + 10 == 23126
    assert eos2 - 10 == 22851
    # R=S=23000; EOR+2 = 23100+16; EOS-2 = 22900-39.
    rows = [
        _row(22850, pe_ltp=1.5, pe_volume=10),
        _row(22900, pe_ltp=39.0, pe_volume=10),
        _row(22950, pe_ltp=38.0, pe_volume=10),
        _row(23000, ce_ltp=40.0, pe_ltp=37.0, ce_volume=5000, pe_volume=5000),
        _row(23050, ce_ltp=14.75, pe_volume=10),
        _row(23100, ce_ltp=16.0, pe_volume=10),
    ]
    lines = freeze_lines(_snapshot(23005, rows))
    assert lines is not None
    assert lines.resistance == 23000
    assert lines.support == 23000
    assert lines.eor2 == 23116
    assert lines.eos2 == 22861
    assert lines.sl_pe_spot == 23126
    assert lines.sl_ce_spot == 22851


# --- §9 walk / tie -----------------------------------------------------------


def test_resistance_closer_max_wins_on_volume_tie():
    rows = [
        _row(24500, ce_volume=1000),
        _row(24550, ce_volume=2500),
        _row(24600, ce_volume=2500),
    ]
    snap = _snapshot(24510, rows)
    strike, volume = walk_resistance(snap, 24500)
    assert strike == 24550
    assert volume == 2500


def test_support_first_peak_not_farther_higher_volume():
    rows = [
        _row(24500, pe_volume=800),
        _row(24450, pe_volume=800),
        _row(24400, pe_volume=2000),
    ]
    snap = _snapshot(24490, rows)
    strike, volume = walk_support(snap, 24500)
    assert strike == 24500
    assert volume == 800


def test_atm_equal_distance_picks_lower():
    assert atm_spot(24525, [24500, 24550]) == 24500


# --- §9 missing line / freeze ------------------------------------------------


def _wide_chain(
    *,
    r_extra_ce: float | None = 18.0,
    include_24550_ce: bool = True,
) -> list[StrikeRow]:
    rows = [
        _row(24350, pe_ltp=40.0, pe_volume=50, ce_volume=10),
        _row(24400, pe_ltp=30.0, pe_volume=50, ce_volume=10),
        _row(24450, pe_ltp=22.0, pe_volume=50, ce_volume=10),
        _row(24500, ce_ltp=35.5, pe_ltp=24.0, ce_volume=5000, pe_volume=5000),
        _row(24550, ce_ltp=r_extra_ce, pe_ltp=20.0, ce_volume=80, pe_volume=40),
        _row(24600, ce_ltp=16.0, pe_ltp=18.0, ce_volume=40, pe_volume=20),
    ]
    if not include_24550_ce:
        rows[4] = _row(24550, ce_ltp=None, pe_ltp=20.0, ce_volume=None, pe_volume=40)
    return rows


def test_missing_eor1_disables_puts():
    lines = freeze_lines(_snapshot(24510, _wide_chain(include_24550_ce=False)))
    assert lines is not None
    assert lines.resistance == 24500
    assert lines.eor is not None
    assert lines.eor1 is None
    assert lines.put_ok is False


def test_no_volume_walk_disables_that_side():
    rows = [
        _row(24500, ce_volume=None, pe_volume=None, ce_ltp=20.0, pe_ltp=20.0),
        _row(24550, ce_volume=None, pe_volume=20.0),
        _row(24450, ce_volume=20.0, pe_volume=None),
    ]
    lines = freeze_lines(_snapshot(24500, rows))
    assert lines is not None
    assert lines.resistance is None
    assert lines.support is None
    assert lines.put_ok is False
    assert lines.call_ok is False


def test_ltp_at_or_below_one_is_not_reversed():
    rows = [
        _row(24500, ce_ltp=35.5, pe_ltp=24.0, ce_volume=5000, pe_volume=5000),
        _row(24550, ce_ltp=1.0, pe_ltp=20.0, ce_volume=80),
        _row(24600, ce_ltp=16.0, pe_volume=20),
        _row(24450, pe_ltp=0.8, pe_volume=80),
        _row(24400, pe_ltp=22.0, pe_volume=20),
    ]
    lines = freeze_lines(_snapshot(24500, rows))
    assert lines is not None
    assert lines.eor1 is None
    assert lines.eos1 is None
    assert lines.put_ok is False
    assert lines.call_ok is False


def test_freeze_labels_proxy_and_does_not_guess_greeks():
    lines = freeze_lines(_snapshot(24510, _wide_chain()))
    assert lines is not None
    assert lines.reversal_mode == "proxy_k_plus_minus_ltp"
    assert lines.eor == 24500 + 35.5
    assert lines.eos == 24500 - 24.0


# --- §9 dual tag / one-and-done / entries ------------------------------------


def _forced_lines(*, call_ok: bool = True, put_ok: bool = True) -> FrozenLines:
    return FrozenLines(
        spot_at_freeze=24510,
        atm=24500,
        expiry="2026-04-30",
        resistance=24500,
        support=24500,
        r_volume=5000,
        s_volume=5000,
        eor=24535.5,
        eor1=24568.0,
        eor2=24616.0,
        eos=24476.0,
        eos1=24428.0,
        eos2=24378.0,
        tgt_ce=24468.0,
        tgt_pe=24530.0,
        sl_ce_spot=24368.0,
        sl_pe_spot=24626.0,
        call_ok=call_ok,
        put_ok=put_ok,
    )


def _entry_state(lines: FrozenLines | None = None) -> SessionState:
    state = SessionState()
    state.session_date = _now(9, 30).date()
    state.state = "FROZEN"
    state.lines = lines or _forced_lines()
    return state


def test_dual_tag_takes_neither():
    # Pathological: EOS-1 above EOR+1 so one spot can tag both.
    lines = FrozenLines(
        spot_at_freeze=24510,
        atm=24500,
        expiry="2026-04-30",
        resistance=24500,
        support=24500,
        r_volume=1,
        s_volume=1,
        eor=24510.0,
        eor1=24520.0,
        eor2=24540.0,
        eos=24500.0,
        eos1=24530.0,
        eos2=24480.0,
        tgt_ce=24540.0,
        tgt_pe=24490.0,
        sl_ce_spot=24470.0,
        sl_pe_spot=24550.0,
        call_ok=True,
        put_ok=True,
    )
    snap = _snapshot(24525, _wide_chain())
    decision = evaluate_entry(_now(10, 0), 24525, snap, _entry_state(lines))
    assert decision.action == "none"
    assert decision.reason == "dual_tag"


def test_ce_entry_on_eos1_and_pe_entry_on_eor1():
    snap = _snapshot(24420, _wide_chain())
    ce = evaluate_entry(_now(10, 0), 24420, snap, _entry_state())
    assert ce.action == "enter"
    assert ce.side == "CE"
    assert ce.lots == 1
    assert ce.quantity == LOT
    assert ce.symbol.endswith("CE")

    snap_pe = _snapshot(24580, _wide_chain())
    pe = evaluate_entry(_now(10, 0), 24580, snap_pe, _entry_state())
    assert pe.action == "enter"
    assert pe.side == "PE"
    assert pe.quantity == LOT
    assert pe.symbol.endswith("PE")


def test_stay_neutral_until_the_line():
    snap = _snapshot(24510, _wide_chain())
    decision = evaluate_entry(_now(10, 0), 24510, snap, _entry_state())
    assert decision.action == "none"
    assert decision.reason == "no_entry"


def test_one_and_done_after_ce_stop_ignores_later_pe():
    engine = LtpFadeEngine()
    freeze_chain = _snapshot(24510, _wide_chain())
    first = engine.tick(_now(9, 21), 24510, chain=freeze_chain)
    assert first.reason in {"no_entry", "no_chain_for_entry"} or engine.state.state == "FROZEN"
    assert engine.state.state == "FROZEN"
    frozen_r = engine.state.lines.resistance if engine.state.lines else None
    frozen_s = engine.state.lines.support if engine.state.lines else None

    later_chain = _snapshot(
        24420,
        _wide_chain()
        + [_row(24700, ce_volume=99_000, pe_volume=99_000)],
    )
    enter = engine.tick(_now(10, 0), 24420, chain=later_chain)
    assert enter.action == "enter"
    assert enter.side == "CE"
    assert engine.state.lines is not None
    assert engine.state.lines.resistance == frozen_r
    assert engine.state.lines.support == frozen_s

    stop = engine.tick(_now(10, 0), 24300, option_ltp=18.0)
    assert stop.action == "exit"
    assert stop.reason == "level_stop"
    assert engine.state.entered_today is True

    again = engine.tick(_now(11, 0), 24600, chain=_snapshot(24600, _wide_chain()))
    assert again.action == "none"
    assert engine.state.state == "DONE"


# --- exits -------------------------------------------------------------------


def _in_trade(side: str, fill: float = 120.0) -> SessionState:
    state = _entry_state()
    state.state = "IN_TRADE"
    state.entered_today = True
    state.side = side  # type: ignore[assignment]
    state.entry_symbol = f"NIFTY30APR2624500{side}"
    state.entry_fill = fill
    state.lot_size = LOT
    return state


def test_premium_stop_at_half_fill():
    decision = evaluate_exit(_now(10, 0), 24500, 59.5, _in_trade("CE", 120.0))
    assert decision.action == "exit"
    assert decision.reason == "premium_sl"


def test_ce_target_and_pe_target():
    ce = evaluate_exit(_now(10, 0), 24468.0, 130.0, _in_trade("CE"))
    assert ce.action == "exit" and ce.reason == "target"
    pe = evaluate_exit(_now(10, 0), 24530.0, 130.0, _in_trade("PE"))
    assert pe.action == "exit" and pe.reason == "target"


def test_level_stops():
    ce = evaluate_exit(_now(10, 0), 24367.9, 100.0, _in_trade("CE"))
    assert ce.action == "exit" and ce.reason == "level_stop"
    pe = evaluate_exit(_now(10, 0), 24626.1, 100.0, _in_trade("PE"))
    assert pe.action == "exit" and pe.reason == "level_stop"


def test_eod_flatten_at_1529():
    decision = evaluate_exit(_now(15, 29), 24500, 110.0, _in_trade("CE"))
    assert decision.action == "exit"
    assert decision.reason == "eod"


def test_no_entry_after_1500_or_before_freeze():
    snap = _snapshot(24420, _wide_chain())
    early = evaluate_entry(_now(9, 20), 24420, snap, _entry_state())
    late = evaluate_entry(_now(15, 0), 24420, snap, _entry_state())
    assert early.reason == "outside_entry_window"
    assert late.reason == "outside_entry_window"


def test_missing_extension_stop_is_disabled_but_premium_remains():
    lines = _forced_lines()
    lines = FrozenLines(**{**lines.__dict__, "eos2": None, "sl_ce_spot": None})
    state = _in_trade("CE", 120.0)
    state.lines = lines
    hold = evaluate_exit(_now(10, 0), 24000.0, 100.0, state)
    assert hold.action == "none"
    assert hold.reason == "hold"
    cut = evaluate_exit(_now(10, 0), 24500.0, 50.0, state)
    assert cut.reason == "premium_sl"


# --- paper engine clock / persist / adapter ----------------------------------


def test_naive_clock_is_fail_closed():
    engine = LtpFadeEngine()
    naive = datetime(2026, 4, 30, 9, 21)
    decision = engine.tick(naive, 24510, chain=_snapshot(24510, _wide_chain()))
    assert decision.reason == "clock_not_ist"


def test_freeze_retry_then_no_trade():
    engine = LtpFadeEngine()
    waiting = engine.tick(_now(9, 22), 24510, chain=None)
    assert waiting.reason == "freezing"
    dead = engine.tick(_now(9, 30), 24510, chain=None)
    assert dead.reason == "freeze_failed"
    assert engine.state.state == "NO_TRADE"


def test_paper_path_freezes_once_from_recorded_payload(tmp_path):
    as_of = _now(9, 21)
    payload = {
        "status": "success",
        "underlying_ltp": 24510,
        "expiry_date": "2026-04-30",
        "atm_strike": 24500,
        "chain": [
            {
                "strike": 24400,
                "ce": {"symbol": "NIFTY30APR2624400CE", "ltp": 80, "volume": 10, "lotsize": LOT},
                "pe": {"symbol": "NIFTY30APR2624400PE", "ltp": 22, "volume": 20, "lotsize": LOT},
            },
            {
                "strike": 24450,
                "ce": {"symbol": "NIFTY30APR2624450CE", "ltp": 55, "volume": 10, "lotsize": LOT},
                "pe": {"symbol": "NIFTY30APR2624450PE", "ltp": 22, "volume": 40, "lotsize": LOT},
            },
            {
                "strike": 24500,
                "ce": {
                    "symbol": "NIFTY30APR2624500CE",
                    "ltp": 35.5,
                    "volume": 5000,
                    "lotsize": LOT,
                },
                "pe": {
                    "symbol": "NIFTY30APR2624500PE",
                    "ltp": 24.0,
                    "volume": 5000,
                    "lotsize": LOT,
                },
            },
            {
                "strike": 24550,
                "ce": {"symbol": "NIFTY30APR2624550CE", "ltp": 18, "volume": 80, "lotsize": LOT},
                "pe": {"symbol": "NIFTY30APR2624550PE", "ltp": 40, "volume": 30, "lotsize": LOT},
            },
            {
                "strike": 24600,
                "ce": {"symbol": "NIFTY30APR2624600CE", "ltp": 16, "volume": 40, "lotsize": LOT},
                "pe": {"symbol": "NIFTY30APR2624600PE", "ltp": 60, "volume": 10, "lotsize": LOT},
            },
        ],
    }
    paper = PaperFade(snapshot_dir=tmp_path)
    decision = paper.on_tick(as_of, 24510, chain_payload=payload)
    assert paper.engine.state.state == "FROZEN"
    assert paper.engine.state.lines is not None
    assert paper.engine.state.lines.resistance == 24500
    assert paper.engine.state.lines.support == 24500
    assert (tmp_path / "ltp-fade-2026-04-30.json").exists()
    assert "proxy_k_plus_minus_ltp" in " ".join(paper.engine.state.logs)
    assert decision.action == "none"

    moved = dict(payload)
    moved["chain"] = list(payload["chain"])
    moved["chain"][4] = {
        "strike": 24600,
        "ce": {"symbol": "NIFTY30APR2624600CE", "ltp": 16, "volume": 99_000, "lotsize": LOT},
        "pe": {"symbol": "NIFTY30APR2624600PE", "ltp": 60, "volume": 99_000, "lotsize": LOT},
    }
    paper.on_tick(_now(10, 0), 24510, chain_payload=moved)
    assert paper.engine.state.lines.resistance == 24500
    assert paper.engine.state.lines.support == 24500


def test_adapter_accepts_openalgo_ce_pe_shape():
    payload = {
        "underlying_ltp": 24310,
        "expiry_date": "2026-04-30",
        "chain": [
            {
                "strike": 24300,
                "ce": {"symbol": "XCE", "ltp": 35.5, "volume": 100, "oi": 1, "lotsize": 75},
                "pe": {"symbol": "XPE", "ltp": 2.1, "volume": 90, "oi": 2, "lotsize": 75},
            }
        ],
    }
    snap = snapshot_from_chain(payload, as_of=_now(9, 21))
    assert snap is not None
    assert snap.spot == 24310
    assert snap.rows[0].ce_ltp == 35.5
    assert snap.rows[0].lot_size == 75


def test_fade_tag_sets_strategist_side():
    from strike_desk.options_strategist import direction_from_regime
    from strike_desk.specialists import SpecialistRequest

    req = SpecialistRequest(
        tick_id="t",
        index_symbol="NIFTY",
        as_of=_now(10, 0),
        book={
            "ltp_fade": {"tag": "PE"},
            "regime": {"rationale": "bullish daily side"},
        },
    )
    assert direction_from_regime(req) == ("bearish", "PE")
    req.book["ltp_fade"]["tag"] = "CE"
    assert direction_from_regime(req) == ("bullish", "CE")


def test_jev_allow_plus_fade_tag_skips_adx_block(settings):
    from strike_desk.graph import _decide_outcome, _wants_a_contract

    state = {
        "book": {
            "open_positions": [],
            "ltp_fade": {
                "state": "FROZEN",
                "tag": "CE",
                "entered_today": False,
                "dual_tag": False,
            },
        },
        "regime_label": "trending",
        "regime_confidence": 0.80,
        "trend_snapshot": {
            "daily_plus_di": 12.4,
            "daily_minus_di": 18.1,
            "daily_adx": 18.2,
            "ten_plus_di": 14.0,
            "ten_minus_di": 20.0,
            "ten_adx": 22.0,
        },
    }
    assert _wants_a_contract(state, settings) is True
    outcome, code, _text = _decide_outcome(state, settings)
    assert code != "regime-weak-trend"
    assert outcome == "decline"
    assert code == "specialist-unavailable"


def test_jev_not_tradeable_still_blocks_a_fade_tag(settings):
    from strike_desk.graph import _wants_a_contract

    state = {
        "book": {
            "open_positions": [],
            "ltp_fade": {"state": "FROZEN", "tag": "PE", "entered_today": False},
        },
        "regime_label": "event-driven",
        "regime_confidence": 0.90,
    }
    assert _wants_a_contract(state, settings) is False


def test_fade_exit_trailer_round_trip():
    from strike_desk.ltp_fade.exits import fade_exit_reason, format_trailer, parse_trailer

    text = format_trailer("CE", 24468.0, 24368.0)
    exits = parse_trailer(f"proposal text {text}")
    assert exits is not None
    assert exits.side == "CE"
    assert exits.tgt_spot == 24468.0
    assert fade_exit_reason(_now(10, 0), 24468.0, 100.0, 120.0, exits) == "target"
    assert fade_exit_reason(_now(10, 0), 24300.0, 100.0, 120.0, exits) == "level_stop"
    assert fade_exit_reason(_now(15, 29), 24400.0, 100.0, 120.0, exits) == "eod"
    assert fade_exit_reason(_now(10, 0), 24400.0, 50.0, 120.0, exits) == "premium_sl"


def test_current_week_expiry_picks_next_live():
    from datetime import date

    from strike_desk.ltp_fade.session import current_week_expiry

    token = current_week_expiry(["30-SEP-26", "06-OCT-26", "13-OCT-26"], date(2026, 10, 4))
    assert token == "06OCT26"


def test_no_tradetron_types_in_package():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "strike_desk" / "ltp_fade"
    banned = ("tt_get_instrument", "tt_", "Get Runtime Number", "wr_ready")
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    for token in banned:
        assert token not in text
