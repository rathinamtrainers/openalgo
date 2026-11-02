"""Reconnect must run broker OAuth when the stored token is gone."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blueprints.brlogin import reconnect_action  # noqa: E402


def test_live_token_still_skips_to_dashboard():
    assert reconnect_action(True, True, False) == "dashboard"
    assert reconnect_action(True, True, True) == "dashboard"


def test_expired_token_with_kite_payload_runs_oauth():
    assert reconnect_action(True, False, True) is None


def test_expired_token_without_payload_goes_to_broker_page():
    assert reconnect_action(True, False, False) == "broker"


def test_password_only_session_runs_oauth():
    assert reconnect_action(False, False, True) is None
    assert reconnect_action(False, False, False) is None
