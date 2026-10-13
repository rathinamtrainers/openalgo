"""Place-timeout recovery never retries the wire and never mistakes a long for a miss."""

from __future__ import annotations

from strike_desk.errors import OpenAlgoError
from strike_desk.fill_recovery import is_place_timeout, long_option_lots, matching_long


def test_read_timeout_is_a_place_timeout() -> None:
    assert is_place_timeout(OpenAlgoError("/api/v1/placeorder: transport failure ReadTimeout"))
    assert not is_place_timeout(OpenAlgoError("/api/v1/placeorder: HTTP 500"))


def test_long_option_lots_ignore_shorts_and_equity() -> None:
    book = [
        {"symbol": "NIFTY06OCT2622800PE", "quantity": 65},
        {"symbol": "NIFTY06OCT2622800CE", "quantity": -65},
        {"symbol": "RELIANCE", "quantity": 10},
        {"symbol": "NIFTY06OCT2622800PE", "quantity": 0},
    ]
    lots = long_option_lots(book, index_symbol="NIFTY")
    assert [row["symbol"] for row in lots] == ["NIFTY06OCT2622800PE"]


def test_matching_long_finds_the_intended_symbol() -> None:
    book = [
        {"symbol": "NIFTY06OCT2622800PE", "quantity": 65},
        {"symbol": "NIFTY06OCT2622900PE", "quantity": 65},
    ]
    found = matching_long(book, "nifty06oct2622800pe")
    assert found is not None and found["quantity"] == 65
    assert matching_long(book, "NIFTY06OCT2623000PE") is None
