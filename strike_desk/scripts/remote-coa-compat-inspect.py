#!/usr/bin/env python3
import inspect
import sys

sys.path.insert(0, "/opt/strike-desk/current/strike_desk/src")
from strike_desk.coa_boards import CoaBoard, NIFTY  # noqa: E402
from strike_desk.ltp_coa import evaluate  # noqa: E402

print("evaluate", inspect.signature(evaluate))
print("board_fields", list(CoaBoard.__dataclass_fields__))
print("nifty_session", getattr(NIFTY, "session", None))
print("nifty_always_open", getattr(NIFTY, "always_open", None))
print("has_order_quantity", hasattr(NIFTY, "order_quantity"))
