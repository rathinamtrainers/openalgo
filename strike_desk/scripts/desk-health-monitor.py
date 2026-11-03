#!/usr/bin/env python3
# ruff: noqa: E402,I001
"""HPZBook / GCE launcher for the read-only Strike Desk health monitor.

Does not place or cancel orders. Does not pulse Hands.

From the worktree or a clone:

    uv run --project strike_desk python -m strike_desk.desk_health
    uv run --project strike_desk python strike_desk/scripts/desk-health-monitor.py
    uv run --project strike_desk python -m strike_desk.desk_health --remote
    uv run --project strike_desk python -m strike_desk.desk_health --loop 60
    uv run --project strike_desk python -m strike_desk.desk_health --remote --hourly --alert

On GCE (after scp of desk_health.py):

    python3 /tmp/desk_health_probe.py --probe
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strike_desk.desk_health import main  # noqa: E402,I001


if __name__ == "__main__":
    raise SystemExit(main())
