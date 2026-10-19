"""Public /ltp brief: CoA map in English, no private Hands fields."""

from datetime import datetime
from zoneinfo import ZoneInfo

from strike_desk.ltp_coa import evaluate
from strike_desk.ltp_coa_brief import (
    DISCLAIMER,
    SessionTape,
    build_brief,
    update_tape,
    write_public_brief,
)

IST = ZoneInfo("Asia/Kolkata")
ATM = 24050.0
STEP = 50.0
WINDOW = datetime(2026, 10, 16, 10, 0, tzinfo=IST)
AFTER = datetime(2026, 10, 16, 15, 30, tzinfo=IST)

_PRIVATE = ("1.1", "1.8", "ltp_buy", "QuadShield", "Hands", "999183250", "webhook")


def _row(strike: float, *, ce_vol=0, ce_oi=0, pe_vol=0, pe_oi=0) -> dict:
    return {
        "strike": strike,
        "ce": {"volume": ce_vol, "oi": ce_oi},
        "pe": {"volume": pe_vol, "oi": pe_oi},
    }


def _ladder(atm: float = ATM, **peaks: float) -> list[dict]:
    rows = []
    for i in range(-6, 7):
        strike = atm + i * STEP
        rows.append(_row(strike))
    by_strike = {row["strike"]: row for row in rows}
    for key, value in peaks.items():
        side, metric, strike_s = key.split("_")
        strike = float(strike_s)
        field = "volume" if metric == "vol" else "oi"
        by_strike[strike][side][field] = value
    return [by_strike[k] for k in sorted(by_strike)]


def _strong_chain() -> list[dict]:
    return _ladder(
        pe_vol_24000=1000,
        pe_vol_23900=400,
        ce_vol_24100=1000,
        ce_vol_24200=400,
    )


def _assert_public(payload: dict) -> None:
    blob = " ".join(
        [
            payload["body"],
            payload["mood"],
            payload["disclaimer"],
            str(payload["opportunities"]),
        ]
    )
    for token in _PRIVATE:
        assert token not in blob
    assert payload["disclaimer"] == DISCLAIMER
    assert payload["destination"] == "https://zerotouchalgo.com/ltp"


def test_waiting_between_extensions_is_watching():
    snap = evaluate(_strong_chain(), spot=24050, atm=ATM, extension_pts=20)
    tape = update_tape(None, snap, WINDOW)
    payload = build_brief(snap, WINDOW, tape)
    assert payload["stance"] == "watching"
    assert payload["eos"] == 23980
    assert payload["eor"] == 24120
    sides = {row["side"]: row for row in payload["opportunities"]}
    assert sides["call"]["status"] == "waiting"
    assert sides["call"]["trigger"] == "NIFTY at or below 23,980"
    assert sides["put"]["status"] == "waiting"
    assert "floor at 24,000" in payload["body"]
    assert "call buy needs a print at or below 23,980" in payload["body"]
    _assert_public(payload)


def test_spot_at_eos_is_in_a_trade_and_call_tagged():
    snap = evaluate(_strong_chain(), spot=23980, atm=ATM, extension_pts=20)
    tape = update_tape(None, snap, WINDOW)
    payload = build_brief(snap, WINDOW, tape)
    assert payload["stance"] == "in a trade"
    assert tape.tagged_eos is True
    sides = {row["side"]: row for row in payload["opportunities"]}
    assert sides["call"]["status"] == "tagged"
    assert sides["put"]["status"] == "waiting"
    assert "tagged the call-buy zone" in payload["body"]
    _assert_public(payload)


def test_mixed_map_stands_aside_with_no_buys():
    chain = _ladder(
        22650,
        pe_vol_22650=1000,
        pe_vol_22600=800,
        ce_vol_22700=1000,
        ce_vol_22800=800,
    )
    snap = evaluate(chain, spot=22630, atm=22650, extension_pts=20)
    tape = update_tape(None, snap, WINDOW)
    payload = build_brief(snap, WINDOW, tape)
    assert payload["stance"] == "standing aside"
    assert payload["opportunities"] == []
    assert "No buy is on the map" in payload["body"]
    _assert_public(payload)


def test_after_hours_blocks_setups_and_points_at_next_window():
    snap = evaluate(_strong_chain(), spot=24050, atm=ATM, extension_pts=20)
    tape = update_tape(None, snap, AFTER)
    payload = build_brief(snap, AFTER, tape)
    assert payload["stance"] == "standing aside"
    assert all(row["status"] == "blocked" for row in payload["opportunities"])
    assert "09:20" in payload["body"]
    _assert_public(payload)


def test_write_public_brief_is_world_readable(tmp_path):
    snap = evaluate(_strong_chain(), spot=24050, atm=ATM, extension_pts=20)
    tape = SessionTape(session_date="2026-10-16", open_spot=24050, high=24050, low=24050)
    payload = build_brief(snap, WINDOW, tape)
    path = tmp_path / "public-brief.json"
    write_public_brief(path, payload)
    text = path.read_text(encoding="utf-8")
    assert "23,980" in text
    assert "ltp_buy" not in text
