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
            payload.get("action") or "",
            payload.get("do_now") or "",
            payload.get("line") or "",
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
    assert payload["computed_at"] == "2026-10-16T10:00:00+05:30"
    assert payload["stance"] == "watching"
    assert payload["eos"] == 23980
    assert payload["eor"] == 24120
    sides = {row["side"]: row for row in payload["opportunities"]}
    assert sides["call"]["status"] == "waiting"
    assert sides["call"]["trigger"] == "NIFTY at or below 23,980"
    assert sides["put"]["status"] == "waiting"
    assert sides["call"]["label"] == "Not reached — wait"
    assert payload["do_now"].startswith("Do nothing yet.")
    assert "23,980" in payload["line"]
    assert "floor at 24,000" in payload["body"]
    assert "call buy needs a print at or below 23,980" in payload["body"]
    _assert_public(payload)


def test_spot_at_eos_is_in_a_trade_but_chip_waits_for_hands():
    snap = evaluate(_strong_chain(), spot=23980, atm=ATM, extension_pts=20)
    tape = update_tape(None, snap, WINDOW)
    payload = build_brief(snap, WINDOW, tape)
    assert payload["stance"] == "in a trade"
    assert payload["scenario_label"] == "WATCHING"
    assert tape.tagged_eos is True
    sides = {row["side"]: row for row in payload["opportunities"]}
    assert sides["call"]["status"] == "waiting"
    assert sides["put"]["status"] == "waiting"
    assert payload["do_now"].startswith("A call buy is armed")
    assert "tagged the call-buy zone" in payload["body"]
    _assert_public(payload)


def test_chip_tags_only_after_hands_post():
    snap = evaluate(_strong_chain(), spot=23980, atm=ATM, extension_pts=20)
    payload = build_brief(
        snap, WINDOW, update_tape(None, snap, WINDOW), hands_posted="CE"
    )
    assert payload["scenario_label"] == "CALL TAGGED"
    sides = {row["side"]: row for row in payload["opportunities"]}
    assert sides["call"]["status"] == "tagged"
    assert sides["call"]["label"] == "Buy armed — this is the go"
    assert sides["put"]["status"] == "waiting"
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
    assert payload["do_now"].startswith("Do nothing.")
    assert "No buy is on the map" in payload["body"]
    _assert_public(payload)


def test_after_hours_blocks_setups_and_points_at_next_window():
    snap = evaluate(_strong_chain(), spot=24050, atm=ATM, extension_pts=20)
    tape = update_tape(None, snap, AFTER)
    payload = build_brief(snap, AFTER, tape)
    assert payload["stance"] == "standing aside"
    assert all(row["status"] == "waiting" for row in payload["opportunities"])
    assert all(row["label"] == "Window closed — wait" for row in payload["opportunities"])
    assert payload["do_now"].startswith("Do nothing.")
    assert payload["cta"] == payload["do_now"]
    assert payload["last"] == payload["spot"]
    assert payload["scenario_label"] == "WATCHING"
    assert "Cash is closed" in payload["line"]
    assert "09:20" in payload["body"]
    assert isinstance(payload["opportunities"][0]["conditions"], str)
    _assert_public(payload)


def test_put_only_map_does_not_advertise_a_call():
    chain = _ladder(
        pe_vol_24000=1000,
        pe_vol_23900=400,
        ce_vol_24100=1000,
        ce_vol_24000=800,
    )
    snap = evaluate(chain, spot=24050, atm=ATM, extension_pts=20)
    payload = build_brief(snap, WINDOW, update_tape(None, snap, WINDOW))
    assert snap.allow_ce is False
    assert snap.allow_pe is True
    sides = {row["side"] for row in payload["opportunities"]}
    assert sides == {"put"}
    assert payload["opportunity_source"] == "coa"
    assert payload["watch_for"] == "24,120 put buy"
    assert "call buy" not in payload["waiting_for"]
    assert payload["building"]["call"] == "no call buy on this map"
    assert payload["strike"] == "24100"
    _assert_public(payload)


def test_push_public_brief_skips_without_token():
    from strike_desk.ltp_coa_brief import push_public_brief

    class _Settings:
        coa_publish_url = "https://zerotouchalgo.com/api/public-brief"
        coa_publish_token = None

    push_public_brief({"line": "x"}, _Settings())


def test_banknifty_brief_does_not_say_nifty():
    snap = evaluate(_strong_chain(), spot=24050, atm=ATM, extension_pts=40)
    payload = build_brief(
        snap,
        WINDOW,
        update_tape(None, snap, WINDOW),
        index_name="BANKNIFTY",
        strike_step=100,
        market="banknifty",
        extension_pts=40,
    )
    assert payload["market"] == "banknifty"
    assert payload["index"] == "BANKNIFTY"
    assert payload["opportunities"][0]["trigger"].startswith("BANKNIFTY ")
    assert payload["do_now"].startswith("Do nothing yet. BANKNIFTY")
    assert "minus 40 points" in payload["disclaimer"]


def test_write_public_brief_is_world_readable(tmp_path):
    snap = evaluate(_strong_chain(), spot=24050, atm=ATM, extension_pts=20)
    tape = SessionTape(session_date="2026-10-16", open_spot=24050, high=24050, low=24050)
    payload = build_brief(snap, WINDOW, tape)
    path = tmp_path / "public-brief.json"
    write_public_brief(path, payload)
    text = path.read_text(encoding="utf-8")
    assert "23,980" in text
    assert "ltp_buy" not in text
