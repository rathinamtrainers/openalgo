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
