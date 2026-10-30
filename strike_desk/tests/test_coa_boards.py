from datetime import date

from strike_desk.coa_boards import (
    BANKNIFTY,
    BTC,
    CASH_BOARDS,
    CRUDEOIL,
    MCX_BOARDS,
    NATGAS,
    NIFTY,
    SENSEX,
    expiry_date_for_board,
    pick_listed_expiry,
)


def test_nifty_weekly_is_tuesday():
    expiry = expiry_date_for_board(date(2026, 10, 26), NIFTY, lambda _day: True)
    assert expiry == date(2026, 10, 27)
    assert NIFTY.flag_suffix == ""
    assert NIFTY.brief_file == "public-brief.json"


def test_banknifty_monthly_is_last_tuesday():
    expiry = expiry_date_for_board(date(2026, 10, 15), BANKNIFTY, lambda _day: True)
    assert expiry == date(2026, 10, 27)
    rolled = expiry_date_for_board(date(2026, 10, 29), BANKNIFTY, lambda _day: True)
    assert rolled == date(2026, 11, 24)
    assert BANKNIFTY.expiry_kind == "monthly"
    assert NIFTY.expiry_kind == "weekly"
    assert BANKNIFTY.flag_suffix == "_bn"
    assert BANKNIFTY.option_exchange == "NFO"


def test_banknifty_monthly_walks_holiday_to_listed_monday():
    def is_open(day: date) -> bool:
        return day != date(2026, 11, 24)

    rolled = expiry_date_for_board(date(2026, 10, 30), BANKNIFTY, is_open)
    assert rolled == date(2026, 11, 23)


def test_pick_listed_expiry_uses_broker_banknifty_monthlies():
    from strike_desk.coa_boards import pick_listed_expiry, parse_expiry_label

    labels = [
        "23-NOV-26",
        "29-DEC-26",
        "25-JAN-27",
        "30-MAR-27",
        "29-JUN-27",
        "28-SEP-27",
    ]
    assert parse_expiry_label("23-NOV-26") == date(2026, 11, 23)
    assert pick_listed_expiry(date(2026, 10, 30), labels, kind="monthly") == date(2026, 11, 23)
    assert pick_listed_expiry(date(2026, 11, 24), labels, kind="monthly") == date(2026, 12, 29)


def test_banknifty_monthly_skips_listed_weeklies():
    from strike_desk.coa_boards import pick_listed_expiry

    mixed = [
        "03-NOV-26",
        "10-NOV-26",
        "17-NOV-26",
        "23-NOV-26",
        "29-DEC-26",
    ]
    assert pick_listed_expiry(date(2026, 10, 30), mixed, kind="weekly") == date(2026, 11, 3)
    assert pick_listed_expiry(date(2026, 10, 30), mixed, kind="monthly") == date(2026, 11, 23)


def test_sensex_weekly_is_thursday_on_bfo():
    expiry = expiry_date_for_board(date(2026, 10, 26), SENSEX, lambda _day: True)
    assert expiry == date(2026, 10, 29)
    assert SENSEX.flag_suffix == "_sx"
    assert SENSEX.option_exchange == "BFO"
    assert SENSEX.index_spot_exchange == "BSE_INDEX"


def test_btc_is_daily_and_always_open():
    expiry = expiry_date_for_board(date(2026, 10, 30), BTC, lambda _day: False)
    assert expiry == date(2026, 10, 30)
    assert BTC.always_open is True
    assert BTC.analyze is False
    assert BTC.slug == "btc-options"
    assert BTC.option_exchange == "CRYPTO"
    assert BTC not in CASH_BOARDS
    assert BTC.flag_suffix == ""


def test_crude_and_natgas_are_mcx_coa_boards():
    assert CRUDEOIL in MCX_BOARDS
    assert NATGAS in MCX_BOARDS
    assert CRUDEOIL not in CASH_BOARDS
    assert CRUDEOIL.option_exchange == "MCX"
    assert CRUDEOIL.index_spot_exchange == "MCX"
    assert CRUDEOIL.flag_suffix == "_cl"
    assert CRUDEOIL.strike_step == 50.0
    assert NATGAS.index_symbol == "NATURALGAS"
    assert NATGAS.flag_suffix == "_ng"
    assert NATGAS.strike_step == 5.0
    assert NATGAS.contract_qty == 1250
    assert NATGAS.order_quantity(1, 1) == 1250
    assert CRUDEOIL.order_quantity(1, 1) == 100
    assert NIFTY.order_quantity(65, 1) == 65
    labels = ["17-NOV-26", "16-DEC-26", "15-JAN-27"]
    assert pick_listed_expiry(date(2026, 10, 30), labels, kind="monthly") == date(2026, 11, 17)
    gas = ["20-NOV-26", "23-DEC-26", "21-JAN-27"]
    assert pick_listed_expiry(date(2026, 10, 30), gas, kind="monthly") == date(2026, 11, 20)
