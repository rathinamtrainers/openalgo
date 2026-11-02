"""Plain-language strategy summaries, optionally polished by a model.

The number-locked template is always written first. A model may rewrite it for
a reader who is not a trader. If the key is missing, the call fails, credits
run out, or the rewrite invents a number, keep the template. Never invent P&L.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

MODEL = "claude-haiku-4-5"
CACHE_TTL_SEC = 300.0
_FORBIDDEN = (
    "fifo",
    "sharpe",
    "sortino",
    "calmar",
    "cagr",
    "roi",
    "profit factor",
    "placeorder",
    "webhook",
    "analyze mode",
    "sandbox engine",
    "round trip",
    "premium deployed",
)

_SYSTEM = (
    "Rewrite each summary for a reader who is not a trader. "
    "Use short everyday words. Talk about money made or lost, trades that "
    "finished, trades still open, and which strategy did best. "
    "Keep every number exactly as given, including the ₹ sign and plus/minus. "
    "Do not add numbers, strategy names, or tickers that are not in the facts. "
    "Do not use trader jargon. "
    "Return JSON only: {\"overall\": \"...\", \"by_id\": {\"id\": \"...\"}}."
)

Invoke = Callable[[str, str], str]
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def attach_narratives(report: dict[str, Any], *, invoke: Invoke | None = None) -> dict[str, Any]:
    """Add a ``narrative`` block. Never raises."""
    try:
        template = _template_block(report)
        polished = _polish(template, invoke=invoke)
        report["narrative"] = polished
    except Exception:
        logger.exception("strategy narrative failed; using empty template")
        report["narrative"] = {
            "source": "template",
            "overall": "Performance numbers are in the table below.",
            "strategies": [],
        }
    return report


def _pct(value: Any) -> str:
    amount = float(value or 0)
    sign = "+" if amount > 0 else ""
    return f"{sign}{amount:.2f}%"


def _money(value: Any) -> str:
    amount = float(value or 0)
    if amount > 0:
        return f"+₹{amount:,.2f}"
    if amount < 0:
        return f"-₹{abs(amount):,.2f}"
    return "₹0.00"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _strategy_line(name: str, summary: dict[str, Any]) -> str:
    fills = _int(summary.get("fills"))
    closed = _int(summary.get("closed_round_trips"))
    wins = _int(summary.get("wins"))
    losses = _int(summary.get("losses"))
    realized = _money(summary.get("realized_pnl"))
    unrealized = float(summary.get("unrealized_pnl") or 0)
    if fills == 0 and closed == 0 and not unrealized:
        return f"{name} has no trades yet."
    trade_word = "trade" if closed == 1 else "trades"
    parts = [f"{name} made {realized} from {closed} finished {trade_word}"]
    if closed:
        parts.append(f"({wins} won, {losses} lost)")
    line = " ".join(parts) + "."
    pf = summary.get("profit_factor")
    if isinstance(pf, (int, float)):
        line += f" Profit ratio {pf:.2f}."
    elif closed and wins and not losses:
        line += " Every finished trade made money."
    calendar_days = _int(summary.get("calendar_days"))
    trading_days = _int(summary.get("trading_days"))
    if calendar_days:
        if trading_days:
            line += f" Over {calendar_days} calendar days ({trading_days} trading days)."
        else:
            line += f" Over {calendar_days} calendar days."
    avg_up = summary.get("avg_profit_day")
    if isinstance(avg_up, (int, float)):
        line += f" Average up day {_money(avg_up)}."
    avg_down = summary.get("avg_loss_day")
    if isinstance(avg_down, (int, float)):
        line += f" Average down day {_money(avg_down)}."
    cagr = summary.get("cagr_potential_pct")
    if isinstance(cagr, (int, float)) and abs(cagr) <= 500:
        line += f" Yearly pace {_pct(cagr)} if this stretch kept going."
    if unrealized:
        line += f" There is also {_money(unrealized)} sitting in trades that are still open."
    return line


def _overall_line(report: dict[str, Any]) -> str:
    summary = (report.get("overall") or {}).get("summary") or {}
    closed = _int(summary.get("closed_round_trips"))
    wins = _int(summary.get("wins"))
    losses = _int(summary.get("losses"))
    realized = _money(summary.get("realized_pnl"))
    rows = []
    for row in report.get("strategies") or []:
        total = float((row.get("performance") or {}).get("summary", {}).get("realized_pnl") or 0)
        if total == 0:
            continue
        rows.append((str(row.get("name") or row.get("id") or ""), total))
    rows.sort(key=lambda item: item[1], reverse=True)
    line = (
        f"Together, the strategies made {realized}. "
        f"{closed} trades are finished ({wins} won, {losses} lost)."
    )
    pf = summary.get("profit_factor")
    if isinstance(pf, (int, float)):
        line += f" Profit ratio {pf:.2f}."
    calendar_days = _int(summary.get("calendar_days"))
    trading_days = _int(summary.get("trading_days"))
    if calendar_days:
        if trading_days:
            line += f" Over {calendar_days} calendar days ({trading_days} trading days)."
        else:
            line += f" Over {calendar_days} calendar days."
    cagr = summary.get("cagr_potential_pct")
    if isinstance(cagr, (int, float)) and abs(cagr) <= 500:
        line += f" Yearly pace {_pct(cagr)} if this stretch kept going."
    if rows:
        best_name, best_pnl = rows[0]
        line += f" {best_name} made the most ({_money(best_pnl)})."
    return line


def _template_block(report: dict[str, Any]) -> dict[str, Any]:
    strategies = []
    for row in report.get("strategies") or []:
        name = str(row.get("name") or row.get("id") or "")
        summary = (row.get("performance") or {}).get("summary") or {}
        fills = _int(summary.get("fills"))
        closed = _int(summary.get("closed_round_trips"))
        unrealized = float(summary.get("unrealized_pnl") or 0)
        if fills == 0 and closed == 0 and not unrealized:
            continue
        strategies.append(
            {
                "id": str(row.get("id") or ""),
                "name": name,
                "text": _strategy_line(name, summary),
            }
        )
    return {
        "source": "template",
        "overall": _overall_line(report),
        "strategies": strategies,
    }


def _polish(template: dict[str, Any], *, invoke: Invoke | None) -> dict[str, Any]:
    worker = invoke if invoke is not None else _default_invoke()
    if worker is None:
        return template
    cache_key = template["overall"] + "|" + "|".join(
        f"{row['id']}:{row['text']}" for row in template["strategies"]
    )
    now = time.monotonic()
    hit = _CACHE.get(cache_key)
    if hit and now - hit[0] < CACHE_TTL_SEC:
        return hit[1]
    try:
        raw = worker(_SYSTEM, _user(template)).strip()
        parsed = _parse(raw)
    except Exception:
        logger.exception("strategy narrative polish failed")
        return template
    if not parsed:
        return template
    overall = str(parsed.get("overall") or "").strip()
    if not _grounded(overall, template["overall"]):
        return template
    by_id = parsed.get("by_id") or {}
    out_rows = []
    for row in template["strategies"]:
        rewritten = str(by_id.get(row["id"]) or "").strip()
        text = rewritten if rewritten and _grounded(rewritten, row["text"]) else row["text"]
        out_rows.append({"id": row["id"], "name": row["name"], "text": text})
    polished = {"source": "llm", "overall": overall, "strategies": out_rows}
    _CACHE[cache_key] = (now, polished)
    return polished


def _user(template: dict[str, Any]) -> str:
    payload = {
        "overall": template["overall"],
        "strategies": [
            {"id": row["id"], "name": row["name"], "text": row["text"]}
            for row in template["strategies"]
        ],
    }
    return "Facts, already safe to show:\n" + json.dumps(payload, ensure_ascii=False)


def _parse(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _grounded(rewritten: str, template: str) -> bool:
    if not rewritten or len(rewritten) > 500:
        return False
    lower = rewritten.lower()
    if any(token in lower for token in _FORBIDDEN):
        return False
    allowed = set(_numbers(template))
    extra = [num for num in _numbers(rewritten) if num not in allowed]
    return not extra


def _numbers(text: str) -> list[str]:
    return [token.rstrip(",") for token in re.findall(r"\d[\d,]*\.?\d*", text)]


def _api_key() -> str | None:
    for name in ("STRIKE_DESK_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    for path in (
        Path("/opt/strike-desk/current/.env"),
        Path("/opt/strike-desk/current/strike_desk/.env"),
    ):
        key = _key_from_env_file(path)
        if key:
            return key
    return None


def _key_from_env_file(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() != "STRIKE_DESK_ANTHROPIC_API_KEY":
                continue
            return value.strip().strip("'").strip('"') or None
    except Exception:
        logger.exception("could not read narrative key file")
    return None


def _default_invoke() -> Invoke | None:
    key = _api_key()
    if not key:
        return None
    try:
        from utils.httpx_client import get_httpx_client
    except Exception:
        return None

    def _call(system: str, user: str) -> str:
        client = get_httpx_client()
        response = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 700,
                "temperature": 0,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=20.0,
        )
        if response.status_code >= 400:
            logger.info("strategy narrative polish skipped http=%s", response.status_code)
            raise RuntimeError("polish http error")
        body = response.json()
        chunks = body.get("content") or []
        return "".join(str(part.get("text") or "") for part in chunks if isinstance(part, dict))

    return _call
