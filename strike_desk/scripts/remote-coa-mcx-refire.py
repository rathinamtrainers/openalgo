#!/usr/bin/env python3
"""If Natgas CoA is still in a CE, post buy_ce_ng to MCX Hands. No secrets printed."""

from __future__ import annotations

import json
from pathlib import Path

from strike_desk.config import get_settings
from strike_desk.tradetron_hands import COA_BUY_CE, COA_BUY_PE, MCX_HANDS_BOARDS, CoaHands

STATE = Path("/var/lib/strike-desk/coa_live_ng.json")


def main() -> None:
    raw = json.loads(STATE.read_text(encoding="utf-8"))
    open_trade = raw.get("open") if isinstance(raw.get("open"), dict) else {}
    side = str(open_trade.get("side") or "").upper()
    qty = int(raw.get("analyzer_qty") or 0)
    if qty <= 0 or side not in {"CE", "PE"}:
        print("refire skipped: not in a natgas trade")
        return
    settings = get_settings()
    secret = settings.tt_auth_token_coa_mcx
    token = secret.get_secret_value().strip() if secret is not None else ""
    if not token:
        raise SystemExit("STRIKE_DESK_TT_AUTH_TOKEN_COA_MCX unset")
    hands = CoaHands(settings, token=token, boards=MCX_HANDS_BOARDS)
    action = COA_BUY_CE if side == "CE" else COA_BUY_PE
    result = hands.send(action, board="natgas")
    print("refire", side, result.http_status)


if __name__ == "__main__":
    main()
