"""Named strategy slots in one Strike Desk host.

One process, many books. Each slot has its own journal and exits. They share the
OpenAlgo session. Isolation is off when only one slot is listed so today's overlay
desk keeps seeing every index lot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import Settings

SlotId = Literal["overlay", "ema120"]

KNOWN_SLOTS: frozenset[str] = frozenset({"overlay", "ema120"})


@dataclass(frozen=True)
class SlotSpec:
    """How one known slot binds onto a host Settings copy."""

    playbook_mode: SlotId
    product: str | None
    prefix: str | None


SPECS: dict[str, SlotSpec] = {
    "overlay": SlotSpec(playbook_mode="overlay", product=None, prefix=None),
    "ema120": SlotSpec(playbook_mode="ema120", product="NRML", prefix="sd-ema120"),
}


def parse_slot_ids(raw: str) -> tuple[str, ...]:
    """Split ``overlay,ema120`` into known ids. Order is the start order."""
    seen: list[str] = []
    for piece in (part.strip().lower() for part in raw.split(",")):
        if not piece:
            continue
        if piece not in KNOWN_SLOTS:
            raise ValueError(
                f"unknown strike-desk slot {piece!r}; known: {', '.join(sorted(KNOWN_SLOTS))}"
            )
        if piece not in seen:
            seen.append(piece)
    if not seen:
        raise ValueError("slots must name at least one book")
    return tuple(seen)


def resolve_slot_ids(settings: Settings) -> tuple[str, ...]:
    """Listed slots, with the legacy ``playbook_mode=ema120`` flag as a single slot."""
    ids = parse_slot_ids(settings.slots)
    if ids == ("overlay",) and settings.playbook_mode == "ema120":
        return ("ema120",)
    return ids


def isolation_active(settings: Settings) -> bool:
    """True when more than one slot is running. Untagged lots are then ignored."""
    return len(parse_slot_ids(settings.slots)) > 1


def strategy_prefixes(settings: Settings) -> tuple[str, ...]:
    """Order tags this slot will claim. Overlay also owns the historic prefix."""
    prefixes = [settings.order_strategy_prefix, f"sd-{settings.strategy_id}"]
    if settings.strategy_id == "overlay":
        prefixes.append("strike-desk")
    out: list[str] = []
    for prefix in prefixes:
        text = str(prefix or "").strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


def owns_strategy_tag(settings: Settings, tag: str | None) -> bool:
    """True when an OpenAlgo strategy field belongs to this slot."""
    text = str(tag or "").strip()
    if not text:
        return False
    for prefix in strategy_prefixes(settings):
        if text == prefix or text.startswith(f"{prefix}:") or text.startswith(f"{prefix}-"):
            return True
    return False


def lot_tag(row: dict) -> str | None:
    raw = row.get("strategy") or row.get("strategy_tag")
    text = str(raw or "").strip()
    return text or None


def bind_slot(base: Settings, slot_id: str) -> Settings:
    """Copy host settings for one slot. The first listed slot keeps the host state dir."""
    if slot_id not in SPECS:
        raise ValueError(f"unknown strike-desk slot {slot_id!r}")
    spec = SPECS[slot_id]
    ids = resolve_slot_ids(base)
    state_dir = base.state_dir if not ids or slot_id == ids[0] else base.state_dir / slot_id
    updates: dict[str, object] = {
        "strategy_id": slot_id,
        "playbook_mode": spec.playbook_mode,
        "host_state_dir": base.state_dir,
        "state_dir": state_dir,
    }
    if spec.product is not None:
        updates["order_product"] = spec.product
    if spec.prefix is not None:
        updates["order_strategy_prefix"] = spec.prefix
    return base.model_copy(update=updates)


__all__ = [
    "KNOWN_SLOTS",
    "SPECS",
    "SlotSpec",
    "bind_slot",
    "isolation_active",
    "lot_tag",
    "owns_strategy_tag",
    "parse_slot_ids",
    "resolve_slot_ids",
    "strategy_prefixes",
]
