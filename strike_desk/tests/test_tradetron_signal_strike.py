import re

from strike_desk.tradetron_hands import build_payload

_STRIKE_SUFFIX = re.compile(r"(\d{2}[A-Z]{3}\d{2})(\d+(?:\.\d+)?)(CE|PE)$")


def _option_strike(symbol: str) -> float | None:
    match = _STRIKE_SUFFIX.search(str(symbol or "").strip().upper())
    return float(match.group(2)) if match else None


def test_option_strike_parser_matches_openalgo_symbols():
    assert _option_strike("NIFTY19OCT2622400PE") == 22400
    assert _option_strike("VEDL25APR24292.5CE") == 292.5
    assert _option_strike("NIFTY 50") is None


def test_hands_payload_still_starts_unnumbered():
    payload = build_payload("token-aaaaaaaa", (("qs_buy", 1),))
    assert payload["key"] == "qs_buy"
    assert payload["value"] == "1"
