"""Delta India env helpers stay off BROKER_API_KEY."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from broker.deltaexchange.api.baseurl import get_delta_api_key, get_delta_api_secret


def test_prefers_dedicated_india_keys(monkeypatch):
    monkeypatch.setenv("DELTA_INDIA_API_KEY", "india-key")
    monkeypatch.setenv("DELTA_INDIA_API_SECRET", "india-secret")
    monkeypatch.setenv("BROKER_API_KEY", "cash-key")
    monkeypatch.setenv("BROKER_API_SECRET", "cash-secret")

    assert get_delta_api_key() == "india-key"
    assert get_delta_api_secret() == "india-secret"


def test_falls_back_to_broker_slot_when_india_unset(monkeypatch):
    monkeypatch.delenv("DELTA_INDIA_API_KEY", raising=False)
    monkeypatch.delenv("DELTA_INDIA_API_SECRET", raising=False)
    monkeypatch.setenv("BROKER_API_KEY", "cash-key")
    monkeypatch.setenv("BROKER_API_SECRET", "cash-secret")

    assert get_delta_api_key() == "cash-key"
    assert get_delta_api_secret() == "cash-secret"
