"""Book state: filtering, parsing, and refusing to guess."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from strike_desk.book_state import read_book_state
from strike_desk.errors import BookStateUnavailable
from tests.conftest import FUNDS, position


def read(client, journal, settings):
    return read_book_state(client, journal, settings, "2026-07-22", datetime.now(tz=UTC))


def test_flat_book(client, journal, settings, openalgo):
    book = read(client, journal, settings)
    assert book.flat is True
    assert book.open_positions == ()
    assert book.available_cash == pytest.approx(482310.55)
    assert book.utilised_margin == pytest.approx(17689.45)


def test_open_index_position_is_detected(client, journal, settings, openalgo):
    openalgo.post("/api/v1/positionbook").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": [position()]})
    )
    book = read(client, journal, settings)
    assert book.flat is False
    assert book.open_positions[0].quantity == 75


@pytest.mark.parametrize(
    "row",
    [
        position(quantity=0),  # squared off
        {**position(), "exchange": "NSE"},  # wrong exchange
        {**position(symbol="BANKNIFTY28JUL2653000CE")},  # different index
    ],
)
def test_irrelevant_rows_are_filtered_out(client, journal, settings, openalgo, row):
    openalgo.post("/api/v1/positionbook").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": [row]})
    )
    assert read(client, journal, settings).flat is True


def test_unparseable_funds_raise_rather_than_default_to_zero(client, journal, settings, openalgo):
    openalgo.post("/api/v1/funds").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {**FUNDS, "availablecash": "n/a"}}
        )
    )
    with pytest.raises(BookStateUnavailable, match="availablecash"):
        read(client, journal, settings)


def test_openalgo_error_becomes_book_state_unavailable(client, journal, settings, openalgo):
    openalgo.post("/api/v1/positionbook").mock(return_value=httpx.Response(503))
    with pytest.raises(BookStateUnavailable):
        read(client, journal, settings)


def test_decisions_today_comes_from_the_journal(client, journal, settings, openalgo):
    from tests.test_journal import make_row

    journal.record_decision(**make_row("tick-a"))
    journal.record_decision(**make_row("tick-b"))
    assert read(client, journal, settings).decisions_today == 2
