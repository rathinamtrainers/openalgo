"""The guards that replace the human. Every one is arithmetic, so every one is a unit test."""

from datetime import UTC, date, datetime, timedelta

import pytest

from strike_desk import autonomy
from strike_desk.config import Settings
from strike_desk.errors import MirrorUnavailable
from strike_desk.openalgo_mirror import ORDER_MODE_AUTO, ORDER_MODE_SEMI_AUTO


class _Mirror:
    def __init__(self, mode: str) -> None:
        self._mode = mode

    def order_mode(self) -> str:
        return self._mode


class _Monitor:
    def __init__(self, alive: bool, age_seconds: float) -> None:
        self._alive = alive
        self.heartbeat_utc = datetime.now(UTC) - timedelta(seconds=age_seconds)

    def is_alive(self) -> bool:
        return self._alive


@pytest.mark.parametrize(
    ("mode", "reported", "ok"),
    [
        ("unattended", ORDER_MODE_AUTO, True),
        ("unattended", ORDER_MODE_SEMI_AUTO, False),
        ("attended", ORDER_MODE_SEMI_AUTO, True),
        ("attended", ORDER_MODE_AUTO, False),
    ],
)
def test_mode_must_agree_with_openalgo(monitor_settings, mode, reported, ok):
    settings = monitor_settings.model_copy(update={"autonomy": mode})
    verdict = autonomy.check_mode(settings, _Mirror(reported))
    assert verdict.ok is ok
    if not ok:
        assert verdict.reason == "autonomy-mode-mismatch"
        assert reported in verdict.detail


def test_an_unreadable_mirror_blocks_rather_than_guesses(monitor_settings):
    class _Broken:
        def order_mode(self):
            raise MirrorUnavailable("database is locked")

    verdict = autonomy.check_mode(monitor_settings, _Broken())
    assert verdict.ok is False
    assert verdict.reason == "autonomy-mode-mismatch"


@pytest.mark.parametrize(
    ("monitor", "ok"),
    [
        (None, False),
        (_Monitor(False, 0), False),
        (_Monitor(True, 900), False),
        (_Monitor(True, 1), True),
    ],
)
def test_the_dead_man_switch(monitor_settings, monitor, ok):
    settings = monitor_settings.model_copy(update={"autonomy": "unattended"})
    verdict = autonomy.check_monitor(settings, monitor, datetime.now(UTC))
    assert verdict.ok is ok


def test_attended_mode_needs_no_monitor(monitor_settings):
    """Attended, a person is the fallback, so a dead monitor does not block an entry."""
    settings = monitor_settings.model_copy(update={"autonomy": "attended"})
    assert autonomy.check_monitor(settings, None, datetime.now(UTC)).ok is True


def test_the_loss_cap_reads_the_journal_not_memory(monitor_settings, journal, seeded_flat_loss):
    settings = monitor_settings.model_copy(
        update={"autonomy": "unattended", "unattended_daily_loss_cap": 500.0}
    )
    verdict = autonomy.check_day(settings, journal, date(2026, 9, 8))
    assert verdict.ok is False
    assert verdict.reason == "daily-loss-cap"


def test_the_loss_cap_is_checked_before_the_trade_count(
    monitor_settings, journal, seeded_flat_loss
):
    """A desk out of budget says so; it never reports that it has room for one more."""
    settings = monitor_settings.model_copy(
        update={
            "autonomy": "unattended",
            "unattended_daily_loss_cap": 500.0,
            "unattended_max_trades_per_day": 1,
        }
    )
    assert autonomy.check_day(settings, journal, date(2026, 9, 8)).reason == "daily-loss-cap"


def test_the_trade_count_caps_the_day(monitor_settings, journal, seeded_two_entries):
    settings = monitor_settings.model_copy(
        update={"autonomy": "unattended", "unattended_max_trades_per_day": 2}
    )
    verdict = autonomy.check_day(settings, journal, date(2026, 9, 8))
    assert verdict.ok is False and verdict.reason == "daily-trade-cap"


def test_every_guard_is_inert_when_attended(monitor_settings, journal, seeded_flat_loss):
    """The whole of this module must be a no-op in the opt-out mode."""
    settings = monitor_settings.model_copy(update={"autonomy": "attended"})
    verdict = autonomy.evaluate(
        settings, _Mirror(ORDER_MODE_SEMI_AUTO), journal, None, date(2026, 9, 8)
    )
    assert verdict.ok is True


def test_unattended_is_the_default(tmp_path, monkeypatch):
    """The iteration's headline: an unconfigured desk runs with no human in the loop."""
    monkeypatch.delenv("STRIKE_DESK_AUTONOMY", raising=False)
    settings = Settings(
        openalgo_api_key="test-key-0123456789",
        openalgo_user="tester",
        openalgo_db_path=tmp_path / "openalgo.db",
        state_dir=tmp_path / "state",
        tick_budget_seconds=90.0,
        specialist_timeout_seconds=25.0,
        strategist_timeout_seconds=35.0,
    )
    assert settings.autonomy == "unattended"
    assert settings.unattended is True


def test_an_upgraded_semi_auto_desk_declines_rather_than_enters(monitor_settings, journal):
    """AC-22: default-unattended against an un-migrated key must stop, not guess."""
    verdict = autonomy.evaluate(
        monitor_settings, _Mirror(ORDER_MODE_SEMI_AUTO), journal, None, date(2026, 9, 8)
    )
    assert verdict.ok is False
    assert verdict.reason == "autonomy-mode-mismatch"
