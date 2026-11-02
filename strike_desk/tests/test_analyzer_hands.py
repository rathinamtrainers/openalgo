from pydantic import SecretStr

from strike_desk.analyzer_hands import (
    ANALYZER_BUY_CE,
    ANALYZER_BUY_PE,
    ANALYZER_EXIT,
    _ANALYZER_MAP,
    notify_analyzer_hands,
)
from strike_desk.tradetron_hands import build_payload


class _Settings:
    tt_auth_token_analyzer = None
    tt_webhook_url = "https://api.tradetron.tech/api?"


def test_analyzer_hands_flags_are_dedicated():
    payload = build_payload("token-aaaaaaaa", _ANALYZER_MAP[ANALYZER_BUY_PE])
    assert payload["key"] == "an_pe"
    assert payload["value"] == "1"
    assert payload["key1"] == "an_ce"
    assert payload["value1"] == "0"
    keys = {pair[0] for pairs in _ANALYZER_MAP.values() for pair in pairs}
    assert keys == {"an_ce", "an_pe", "an_exit"}
    assert "of_long" not in keys
    assert "qs_buy" not in keys
    assert "ltp_buy_ce" not in keys
    assert _ANALYZER_MAP[ANALYZER_BUY_CE][0] == ("an_ce", 1)
    assert _ANALYZER_MAP[ANALYZER_EXIT][0] == ("an_exit", 1)


def test_notify_skips_when_token_unset():
    settings = _Settings()
    assert notify_analyzer_hands(settings, "NIFTY27OCT2622950PE", "entry") is False


def test_notify_skips_unmapped_symbol(monkeypatch):
    settings = _Settings()
    settings.tt_auth_token_analyzer = SecretStr("token-aaaaaaaa")

    def _boom(*_args, **_kwargs):
        raise AssertionError("must not POST")

    monkeypatch.setattr("strike_desk.analyzer_hands.AnalyzerHands.send", _boom)
    assert notify_analyzer_hands(settings, "NIFTY", "entry") is False
