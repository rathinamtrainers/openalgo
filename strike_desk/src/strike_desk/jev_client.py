"""OpenRouter Decisions API client for TypeSafe Jev."""

from __future__ import annotations

from typing import Any

import httpx

from .config import Settings
from .errors import ModelCallFailed


def jev_cost_micros(settings: Settings, input_tokens: int, output_tokens: int) -> int:
    """Cost in USD micro-dollars at Jev's own prices. Output is free."""
    return round(
        input_tokens * settings.jev_price_in_per_mtok
        + output_tokens * settings.jev_price_out_per_mtok
    )


def decide(settings: Settings, state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
    """One Jev decision. Raises ModelCallFailed on a non-200 or a missing answers map."""
    if settings.jev_api_key is None:
        raise ModelCallFailed("STRIKE_DESK_JEV_API_KEY is not configured")
    payload = {"model": settings.jev_model, "state": state, "questions": questions}
    headers = {
        "Authorization": f"Bearer {settings.jev_api_key.get_secret_value()}",
        "Content-Type": "application/json",
        "X-OpenRouter-Title": "Strike Desk",
    }
    try:
        response = httpx.post(
            settings.jev_decisions_url,
            headers=headers,
            json=payload,
            timeout=settings.analyst_deadline_seconds,
        )
    except httpx.HTTPError as exc:
        raise ModelCallFailed(f"Jev request failed: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise ModelCallFailed(f"Jev HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise ModelCallFailed("Jev returned non-JSON") from exc
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        raise ModelCallFailed("Jev response lacked answers")
    return body
