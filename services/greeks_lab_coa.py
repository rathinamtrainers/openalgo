"""Public CoA briefs plus MCX quote tiles for the Lab page. Never Hands tokens."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

BRIEF_DIR = Path(os.environ.get("GREEKS_LAB_BRIEF_DIR", "/var/lib/strike-desk"))
DESK_FALLBACK = os.environ.get("GREEKS_LAB_BRIEF_URL", "https://desk.zerotouchalgo.com")

BOARDS = (
    {
        "slug": "nifty",
        "file": "public-brief.json",
        "title": "NIFTY",
        "exchange": "NFO",
        "underlying": "NIFTY",
        "source": "coa",
    },
    {
        "slug": "banknifty",
        "file": "public-brief-banknifty.json",
        "title": "BANKNIFTY",
        "exchange": "NFO",
        "underlying": "BANKNIFTY",
        "source": "coa",
    },
    {
        "slug": "sensex",
        "file": "public-brief-sensex.json",
        "title": "SENSEX",
        "exchange": "BFO",
        "underlying": "SENSEX",
        "source": "coa",
    },
    {
        "slug": "crudeoil",
        "file": "public-brief-crudeoil.json",
        "title": "CRUDEOIL",
        "exchange": "MCX",
        "underlying": "CRUDEOIL",
        "source": "coa",
    },
    {
        "slug": "natgas",
        "file": "public-brief-natgas.json",
        "title": "NATGAS",
        "exchange": "MCX",
        "underlying": "NATURALGAS",
        "source": "coa",
    },
    {
        "slug": "btc-options",
        "file": "public-brief-btc-options.json",
        "title": "BTC",
        "exchange": None,
        "underlying": None,
        "source": "coa",
    },
)

PUBLIC_KEYS = (
    "market",
    "index",
    "scenario_label",
    "as_of_ist",
    "computed_at",
    "spot",
    "last",
    "open",
    "high",
    "low",
    "change_vs_open",
    "do_now",
    "line",
    "stance",
    "support",
    "resistance",
    "eos",
    "eor",
)

def _missing_row(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": meta["slug"],
        "title": meta["title"],
        "greeks_exchange": meta["exchange"],
        "greeks_underlying": meta["underlying"],
        "source": meta.get("source") or "coa",
        "scenario_label": None,
        "last": None,
        "missing": True,
    }


def _public_row(raw: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    out = {key: raw.get(key) for key in PUBLIC_KEYS}
    out["slug"] = meta["slug"]
    out["title"] = meta["title"]
    out["greeks_exchange"] = meta["exchange"]
    out["greeks_underlying"] = meta["underlying"]
    out["source"] = meta.get("source") or "coa"
    if not out.get("last") and out.get("spot") is not None:
        out["last"] = out["spot"]
    return out


def _read_file(name: str) -> dict[str, Any] | None:
    path = BRIEF_DIR / name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("CoA brief unreadable JSON: %s", name)
        return None
    return data if isinstance(data, dict) else None


def _read_http(name: str) -> dict[str, Any] | None:
    try:
        from utils.httpx_client import get_httpx_client

        client = get_httpx_client()
        response = client.get(
            f"{DESK_FALLBACK}/{name}", timeout=5.0, headers={"Cache-Control": "no-store"}
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        logger.exception("CoA brief HTTP fallback failed: %s", name)
        return None
    return data if isinstance(data, dict) else None


def get_coa_boards(api_key: str | None = None) -> dict[str, Any]:
    del api_key
    boards: list[dict[str, Any]] = []
    for meta in BOARDS:
        filename = meta.get("file")
        raw = _read_file(filename) if filename else None
        if raw is None and filename:
            raw = _read_http(filename)
        if not raw:
            boards.append(_missing_row(meta))
            continue
        boards.append(_public_row(raw, meta))
    return {"status": "success", "boards": boards}
