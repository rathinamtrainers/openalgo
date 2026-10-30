"""Standalone CoA entry for systemd: ``python -m strike_desk.coa_main``.

Kept off ``strike-desk run`` so Hands flags keep moving even if the LLM tick
is busy. Live Auto QuadShield is not this unit.
"""

from __future__ import annotations

import logging
import signal
import sys
import time

from .coa_analyzer import CoaAnalyzer
from .config import get_settings
from .delta_india import DeltaIndiaChain
from .errors import OpenAlgoError
from .ltp_coa_live import make_btc_runner, make_cash_runners, make_mcx_runners
from .observability import Redactor, configure_logging
from .openalgo_client import OpenAlgoClient
from .session import SessionGate
from .tradetron_hands import (
    BTC_HANDS_BOARDS,
    CASH_HANDS_BOARDS,
    MCX_HANDS_BOARDS,
    CoaHands,
    NoopCoaHands,
)

logger = logging.getLogger(__name__)

_STOP = False


def _handle_stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True


def main(argv: list[str] | None = None) -> int:
    del argv
    settings = get_settings()
    if not settings.coa_enabled:
        print("CoA disabled (STRIKE_DESK_COA_ENABLED!=true); exiting", file=sys.stderr)
        return 0

    token = (
        settings.tt_auth_token_coa.get_secret_value().strip()
        if settings.tt_auth_token_coa is not None
        else ""
    )
    if not token:
        print("STRIKE_DESK_TT_AUTH_TOKEN_COA is required for CoA Hands", file=sys.stderr)
        return 1

    btc_token = (
        settings.tt_auth_token_coa_btc.get_secret_value().strip()
        if settings.tt_auth_token_coa_btc is not None
        else ""
    )
    mcx_token = (
        settings.tt_auth_token_coa_mcx.get_secret_value().strip()
        if settings.tt_auth_token_coa_mcx is not None
        else ""
    )
    secrets = [settings.openalgo_api_key.get_secret_value(), token]
    if btc_token:
        secrets.append(btc_token)
    if mcx_token:
        secrets.append(mcx_token)
    redactor = Redactor(secrets)
    configure_logging(settings, redactor)

    client = OpenAlgoClient(settings)
    analyzer = CoaAnalyzer(settings, client)
    delta = DeltaIndiaChain()
    try:
        gate = SessionGate(client, settings)
        if btc_token:
            btc_hands: CoaHands | NoopCoaHands = CoaHands(
                settings, token=btc_token, boards=BTC_HANDS_BOARDS
            )
        else:
            logger.warning(
                "STRIKE_DESK_TT_AUTH_TOKEN_COA_BTC unset; BTC map runs, Hands stay idle"
            )
            btc_hands = NoopCoaHands()
        if mcx_token:
            mcx_hands: CoaHands | NoopCoaHands = CoaHands(
                settings, token=mcx_token, boards=MCX_HANDS_BOARDS
            )
        else:
            logger.warning(
                "STRIKE_DESK_TT_AUTH_TOKEN_COA_MCX unset; MCX map runs, Hands stay idle"
            )
            mcx_hands = NoopCoaHands()
        cash_hands = CoaHands(settings, token=token, boards=CASH_HANDS_BOARDS)
        runners = make_cash_runners(
            settings,
            client,
            cash_hands,
            analyzer=analyzer,
            is_trading_day=gate.is_trading_day,
            trading_window=gate.trading_window,
        )
        runners.extend(
            make_mcx_runners(
                settings,
                client,
                mcx_hands,
                analyzer=analyzer,
                is_trading_day=lambda day: gate.exchange_window(day, "MCX") is not None,
                trading_window=lambda day: gate.exchange_window(day, "MCX"),
            )
        )
        runners.append(make_btc_runner(settings, delta, btc_hands))

        signal.signal(signal.SIGTERM, _handle_stop)
        signal.signal(signal.SIGINT, _handle_stop)
        logger.info(
            "CoA live started boards=%s interval=%ss",
            ",".join(r.board.slug for r in runners),
            settings.coa_interval_seconds,
        )
        while not _STOP:
            for runner in runners:
                try:
                    sent = runner.step()
                    if sent:
                        logger.info(
                            "coa live %s sent %s", runner.board.slug, ",".join(sent)
                        )
                except OpenAlgoError as failed:
                    runner.touch_heartbeat()
                    logger.warning(
                        "CoA %s read failed: %s", runner.board.slug, failed
                    )
            time.sleep(settings.coa_interval_seconds)
    finally:
        analyzer.close()
        client.close()
        delta.close()
    logger.info("CoA live stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
