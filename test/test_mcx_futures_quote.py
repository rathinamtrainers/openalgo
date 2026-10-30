"""MCX/CDS LTP must use a futures contract, not the cash root."""

from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.option_symbol_service import choose_futures_quote_symbol


def test_goldm_uses_matching_nov_fut():
    rows = [
        {"symbol": "GOLDM05OCT26FUT", "expiry": "05-OCT-26"},
        {"symbol": "GOLDM05NOV26FUT", "expiry": "05-NOV-26"},
        {"symbol": "GOLD05DEC26FUT", "expiry": "05-DEC-26"},
    ]
    assert choose_futures_quote_symbol("GOLDM", "05NOV26", rows) == "GOLDM05NOV26FUT"


def test_gold_does_not_steal_goldm():
    rows = [
        {"symbol": "GOLDM05NOV26FUT", "expiry": "05-NOV-26"},
        {"symbol": "GOLD05DEC26FUT", "expiry": "05-DEC-26"},
    ]
    assert choose_futures_quote_symbol("GOLD", "05NOV26", rows) == "GOLD05DEC26FUT"


def test_picks_next_listed_fut_when_option_expiry_has_no_fut():
    rows = [
        {"symbol": "CRUDEOIL19OCT26FUT", "expiry": "19-OCT-26"},
        {"symbol": "CRUDEOIL18NOV26FUT", "expiry": "18-NOV-26"},
    ]
    assert choose_futures_quote_symbol("CRUDEOIL", "05NOV26", rows) == "CRUDEOIL18NOV26FUT"
