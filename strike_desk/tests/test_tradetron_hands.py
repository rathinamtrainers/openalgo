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
