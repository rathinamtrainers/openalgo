"""Paper runner: evaluate ticks, never call placeorder.

Hysteresis shipped: first touch after freeze. No far-side confirmation.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .adapter import snapshot_from_chain
from .engine import LtpFadeEngine
from .types import Decision


class PaperFade:
    """Apply ``LtpFadeEngine`` to recorded or live chain payloads without orders."""

    def __init__(self, snapshot_dir: Path | None = None) -> None:
        self.engine = LtpFadeEngine(snapshot_dir=snapshot_dir)

    def on_tick(
        self,
        now: datetime,
        spot: float,
        chain_payload: dict[str, Any] | None = None,
        option_ltp: float | None = None,
    ) -> Decision:
        chain = snapshot_from_chain(chain_payload, as_of=now) if chain_payload else None
        return self.engine.tick(now, spot, chain=chain, option_ltp=option_ltp)
