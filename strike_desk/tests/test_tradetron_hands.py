from strike_desk.tradetron_hands import BUY_WINGS, EXIT_ALL, build_payload


def test_payload_starts_with_unnumbered_key_value_pair():
    payload = build_payload("token-aaaaaaaa", (("qs_buy", 1), ("qs_sell", 0)))
    assert payload["key"] == "qs_buy"
    assert payload["value"] == "1"
    assert payload["key1"] == "qs_sell"
    assert payload["value1"] == "0"
    assert payload["auth-token"] == "token-aaaaaaaa"


def test_known_actions_are_wired():
    from strike_desk.tradetron_hands import _PAIR_MAP

    assert BUY_WINGS in _PAIR_MAP
    assert EXIT_ALL in _PAIR_MAP
    assert _PAIR_MAP[BUY_WINGS][0] == ("qs_buy", 1)


def test_coa_flags_are_not_quadshield():
    from strike_desk.tradetron_hands import COA_BUY_CE, COA_EXIT, _COA_MAP

    assert _COA_MAP[COA_BUY_CE][0] == ("ltp_buy_ce", 1)
    assert _COA_MAP[COA_EXIT][0] == ("ltp_exit", 1)
    keys = {pair[0] for pairs in _COA_MAP.values() for pair in pairs}
    assert "qs_buy" not in keys


def test_coa_board_flags_do_not_copy_nifty():
    from strike_desk.tradetron_hands import COA_BUY_CE, COA_BUY_PE, COA_EXIT, coa_flag_pairs

    nifty = {pair[0] for pair in coa_flag_pairs(COA_BUY_CE, "")}
    bn = {pair[0] for pair in coa_flag_pairs(COA_BUY_CE, "_bn")}
    sx = {pair[0] for pair in coa_flag_pairs(COA_EXIT, "_sx")}
    assert nifty == {"ltp_buy_ce", "ltp_buy_pe", "ltp_exit"}
    assert bn == {"ltp_buy_ce_bn", "ltp_buy_pe_bn", "ltp_exit_bn"}
    assert sx == {"ltp_exit_sx", "ltp_buy_ce_sx", "ltp_buy_pe_sx"}
    assert nifty.isdisjoint(bn)
    assert nifty.isdisjoint(sx)
    cl = {pair[0] for pair in coa_flag_pairs(COA_BUY_CE, "_cl")}
    ng = {pair[0] for pair in coa_flag_pairs(COA_BUY_PE, "_ng")}
    assert cl == {"ltp_buy_ce_cl", "ltp_buy_pe_cl", "ltp_exit_cl"}
    assert "ltp_buy_pe_ng" in ng
    assert nifty.isdisjoint(cl)
    assert cl.isdisjoint(ng)


def test_cash_hands_reject_btc_board():
    from strike_desk.tradetron_hands import CASH_HANDS_BOARDS, COA_BUY_CE, CoaHands

    hands = CoaHands.__new__(CoaHands)
    hands._token = "cash-token"
    hands._url = "https://api.tradetron.tech/api?"
    hands._boards = CASH_HANDS_BOARDS
    try:
        hands.send(COA_BUY_CE, board="btc-options")
    except ValueError as exc:
        assert "btc-options" in str(exc)
    else:
        raise AssertionError("cash Hands must not post BTC flags")


def test_btc_hands_reject_nifty_board():
    from strike_desk.tradetron_hands import BTC_HANDS_BOARDS, COA_BUY_CE, CoaHands

    hands = CoaHands.__new__(CoaHands)
    hands._token = "btc-token"
    hands._url = "https://api.tradetron.tech/api?"
    hands._boards = BTC_HANDS_BOARDS
    try:
        hands.send(COA_BUY_CE, board="nifty")
    except ValueError as exc:
        assert "nifty" in str(exc)
    else:
        raise AssertionError("BTC Hands must not post Nifty flags")


def test_cash_hands_reject_mcx_board():
    from strike_desk.tradetron_hands import CASH_HANDS_BOARDS, COA_BUY_CE, CoaHands

    hands = CoaHands.__new__(CoaHands)
    hands._token = "cash-token"
    hands._url = "https://api.tradetron.tech/api?"
    hands._boards = CASH_HANDS_BOARDS
    try:
        hands.send(COA_BUY_CE, board="natgas")
    except ValueError as exc:
        assert "natgas" in str(exc)
    else:
        raise AssertionError("cash Hands must not post MCX flags")


def test_mcx_hands_reject_nifty_board():
    from strike_desk.tradetron_hands import COA_BUY_CE, MCX_HANDS_BOARDS, CoaHands

    hands = CoaHands.__new__(CoaHands)
    hands._token = "mcx-token"
    hands._url = "https://api.tradetron.tech/api?"
    hands._boards = MCX_HANDS_BOARDS
    try:
        hands.send(COA_BUY_CE, board="nifty")
    except ValueError as exc:
        assert "nifty" in str(exc)
    else:
        raise AssertionError("MCX Hands must not post Nifty flags")
