"""The model tier for the Regime Analyst, and what a read costs."""

from __future__ import annotations

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage

from .config import Settings
from .errors import ModelCallFailed


def build_regime_model(settings: Settings) -> ChatAnthropic:
    """Claude Haiku 4.5, thinking off, temperature 0 — a classifier, not a deliberator."""
    if settings.anthropic_api_key is None:
        raise ModelCallFailed("STRIKE_DESK_ANTHROPIC_API_KEY is not configured")
    return ChatAnthropic(
        model=settings.regime_model,
        temperature=settings.regime_temperature,
        max_tokens=settings.regime_max_output_tokens,
        timeout=settings.analyst_deadline_seconds,
        max_retries=1,
        stop=None,
        api_key=settings.anthropic_api_key,
    )


def build_strategist_model(settings: Settings) -> ChatAnthropic:
    """Claude Sonnet 5 — the deliberation tier. Temperature 0: a chooser, not a writer."""
    if settings.anthropic_api_key is None:
        raise ModelCallFailed("STRIKE_DESK_ANTHROPIC_API_KEY is not configured")
    return ChatAnthropic(
        model=settings.strategist_model,
        temperature=settings.strategist_temperature,
        max_tokens=settings.strategist_max_output_tokens,
        timeout=settings.strategist_deadline_seconds,
        max_retries=1,
        stop=None,
        api_key=settings.anthropic_api_key,
    )


def token_usage(message: AIMessage) -> tuple[int, int]:
    """Input and output tokens for one model round, whatever shape the metadata takes."""
    usage: dict[str, Any] = dict(message.usage_metadata or {})
    if not usage:
        raw = message.response_metadata.get("usage") or {}
        usage = {
            "input_tokens": raw.get("input_tokens", 0),
            "output_tokens": raw.get("output_tokens", 0),
        }
    return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)


def cost_micros(settings: Settings, input_tokens: int, output_tokens: int) -> int:
    """Cost in USD micro-dollars. A price of $1 per MTok is exactly 1 micro-dollar per token."""
    return round(
        input_tokens * settings.price_in_per_mtok + output_tokens * settings.price_out_per_mtok
    )


def strategist_cost_micros(settings: Settings, input_tokens: int, output_tokens: int) -> int:
    """Cost in USD micro-dollars at the strategist's own tier prices."""
    return round(
        input_tokens * settings.strategist_price_in_per_mtok
        + output_tokens * settings.strategist_price_out_per_mtok
    )
