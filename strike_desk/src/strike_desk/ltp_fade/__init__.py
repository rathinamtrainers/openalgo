"""NIFTY LTP 9:20 Fade — paper-first weekly ATM option buyer.

Fades the official LTP Calculator 9:20 moderate lines using a published-alert
proxy, not the Calculator's unpublished Greeks formula:

    call_reversal(K) = K + CE_LTP(K)
    put_reversal(K)  = K - PE_LTP(K)

Every reversal is labelled ``proxy_k_plus_minus_ltp``. If a number cannot be
computed from chain LTP + strike, that side does not trade.

Fail-closed: stale/missing chain, LTP ≤ 1, no volume on the walk, missing
EOR/EOR+1 or EOS/EOS−1, already entered today, clock outside 09:21–15:00,
or a failed freeze → no entry. One lot, no averaging, flatten by 15:29 IST.

Jev is the allow/veto. This module freezes the 09:21 lines, tags CE/PE, and
owns the exits. It does not implement COA, SOC, weekly L1, or Tradetron types.
"""

from .adapter import snapshot_from_chain
from .config import LtpFadeConfig
from .decide import evaluate_entry, evaluate_exit
from .engine import LtpFadeEngine
from .exits import FadeExits, fade_exit_reason, format_trailer, parse_trailer
from .freeze import freeze_lines
from .paper import PaperFade
from .reversal import REVERSAL_MODE, call_reversal, put_reversal
from .session import FadeSession
from .types import ChainSnapshot, Decision, FrozenLines, SessionState, StrikeRow
from .walk import walk_resistance, walk_support

__all__ = [
    "REVERSAL_MODE",
    "ChainSnapshot",
    "Decision",
    "FadeExits",
    "FadeSession",
    "FrozenLines",
    "LtpFadeConfig",
    "LtpFadeEngine",
    "PaperFade",
    "SessionState",
    "StrikeRow",
    "call_reversal",
    "evaluate_entry",
    "evaluate_exit",
    "fade_exit_reason",
    "format_trailer",
    "freeze_lines",
    "parse_trailer",
    "put_reversal",
    "snapshot_from_chain",
    "walk_resistance",
    "walk_support",
]
