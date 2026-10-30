"""Optional LLM polish of the public /ltp line.

The template sentence is always written first. A model may rewrite it for a
reader who is not a trader. If the key is missing, the call fails, or the
rewrite invents a number / a buy that the facts do not have, keep the template.
Never raises into the Hands loop.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

_FORBIDDEN = (
    "invalidation",
    "ltp_buy",
    "quadshield",
    "webhook",
    "eos",
    "eor",
    "1.1",
    "1.2",
    "1.3",
    "1.4",
    "1.5",
    "1.6",
    "1.7",
    "1.8",
    "1.9",
    "hands",
)

_SYSTEM = (
    "Rewrite the ticker line for a reader who is not a trader. "
    "Return 1 or 2 short sentences. Keep every number exactly as given. "
    "Do not add numbers, tickers, or strategy names. "
    "Never use: invalidation, CoA, EOS, EOR, Hands, scenario, tagged, recipe. "
    "If the facts say do nothing, the first sentence must say Do nothing. "
    "If a buy is armed, say which side and that this page is not a recommendation."
)

Invoke = Callable[[str, str], str]


def polish_brief(
    payload: dict[str, Any],
    settings: Any | None = None,
    *,
    invoke: Invoke | None = None,
) -> dict[str, Any]:
    """Overwrite line/action/do_now when a grounded rewrite is available."""
    template = str(payload.get("line") or payload.get("do_now") or payload.get("action") or "")
    if not template:
        return payload
    worker = invoke if invoke is not None else _default_invoke(settings)
    if worker is None:
        payload["line_source"] = "template"
        return payload
    try:
        rewritten = worker(_SYSTEM, _user(template)).strip()
    except Exception:
        logger.exception("public line polish failed")
        payload["line_source"] = "template"
        return payload
    if not _grounded(rewritten, template):
        payload["line_source"] = "template"
        return payload
    payload["line"] = rewritten
    payload["do_now"] = rewritten
    payload["action"] = rewritten
    body = str(payload.get("body") or "")
    if body.startswith(template):
        payload["body"] = rewritten + body[len(template) :]
    payload["line_source"] = "llm"
    return payload


def _default_invoke(settings: Any | None) -> Invoke | None:
    if settings is None or getattr(settings, "anthropic_api_key", None) is None:
        return None
    try:
        from .model_client import build_regime_model
    except Exception:
        return None

    def _call(system: str, user: str) -> str:
        model = build_regime_model(settings)
        message = model.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        return str(message.content or "")

    return _call


def _user(template: str) -> str:
    return "Facts, already safe to publish:\n" + template


def _grounded(rewritten: str, template: str) -> bool:
    if not rewritten or len(rewritten) > 400:
        return False
    lower = rewritten.lower()
    if any(token in lower for token in _FORBIDDEN):
        return False
    template_nums = set(_numbers(template))
    extra = [num for num in _numbers(rewritten) if num not in template_nums]
    if extra:
        return False
    if template.lower().startswith("do nothing") and "do nothing" not in lower:
        return False
    if "call buy is armed" in template.lower() and "call" not in lower:
        return False
    if "put buy is armed" in template.lower() and "put" not in lower:
        return False
    return True


def _numbers(text: str) -> list[str]:
    return [token.rstrip(",") for token in re.findall(r"\d[\d,]*", text)]
