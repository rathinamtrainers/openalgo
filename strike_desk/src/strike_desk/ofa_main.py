"""Standalone OFA entry for systemd: ``python -m strike_desk.ofa_main``.

Kept separate from ``strike-desk run`` so the depth loop has its own unit,
heartbeat, and restart path — same contract as StrikeShield supervision.
"""

from __future__ import annotations

import logging
import signal
import sys
import time

from .config import get_settings
from .errors import OpenAlgoError
from .observability import Redactor, configure_logging
from .ofa_analyzer import OfaAnalyzer
from .ofa_hands import OfaHands
from .ofa_live import OfaRunner, render
from .openalgo_client import OpenAlgoClient
from .session import SessionGate

logger = logging.getLogger(__name__)

_STOP = False


def _handle_stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True


def main(argv: list[str] | None = None) -> int:
    del argv  # systemd entry; flags live in the env file
    settings = get_settings()
    if not settings.ofa_enabled:
        print("OFA disabled (STRIKE_DESK_OFA_ENABLED!=true); exiting", file=sys.stderr)
        return 0

    secrets = [settings.openalgo_api_key.get_secret_value()]
    token = (
        settings.tt_auth_token_ofa.get_secret_value().strip()
        if settings.tt_auth_token_ofa is not None
        else ""
    )
    if token:
        secrets.append(token)
    redactor = Redactor(secrets)
    configure_logging(settings, redactor)

    client = OpenAlgoClient(settings)
    analyzer = OfaAnalyzer(settings, client)
    hands = OfaHands(settings) if token else None
    if hands is None:
        logger.warning("OFA Hands token missing — mind will run without Tradetron posts")
    gate = SessionGate(client, settings)
    runner = OfaRunner(
        settings,
        client,
        hands=hands,
        analyzer=analyzer,
        expiry_date_for=gate.expiry_date_for,
        is_trading_day=gate.is_trading_day,
        trading_window=gate.trading_window,
    )

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    logger.info(
        "OFA service started interval=%ss symbol=%r",
        settings.ofa_interval_seconds,
        settings.ofa_symbol or "(monthly fut)",
    )
    try:
        while not _STOP:
            try:
                snap = runner.step()
                logger.info("%s", render(snap, runner.last_actions).replace("\n", " | "))
            except OpenAlgoError as exc:
                # Still prove liveness so a transient OpenAlgo blip is not a restart storm.
                runner.touch_heartbeat()
                logger.warning("OFA read failed: %s", exc)
            time.sleep(settings.ofa_interval_seconds)
    finally:
        analyzer.close()
        client.close()
    logger.info("OFA service stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
