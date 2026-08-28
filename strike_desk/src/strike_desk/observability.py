"""Tracing, redaction and logging — the AgentOps layer this slice owns."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.trace import Tracer

from .config import Settings
from .journal import Journal

logger = logging.getLogger(__name__)

REDACTED = "***redacted***"
SENSITIVE_KEYS = frozenset(
    {"apikey", "api_key", "authorization", "auth", "token", "secret", "password", "pepper"}
)


class Redactor:
    """Removes known secret values and sensitive keys from anything about to be persisted."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        unique = {s for s in secrets if s and len(s) >= 8}
        self._secrets = tuple(sorted(unique, key=len, reverse=True))

    def __call__(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: REDACTED if str(key).lower() in SENSITIVE_KEYS else self(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self(item) for item in value]
        if isinstance(value, str):
            cleaned = value
            for secret in self._secrets:
                cleaned = cleaned.replace(secret, REDACTED)
            return cleaned
        return value


class RedactingFilter(logging.Filter):
    """Applies the redactor to every log record before a handler sees it."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redactor(record.msg)
        if isinstance(record.args, dict):
            record.args = self._redactor(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(self._redactor(arg) for arg in record.args)
        return True


class JournalSpanProcessor(SpanProcessor):
    """Persists every finished span into the append-only ``traces`` table."""

    def __init__(self, journal: Journal, redactor: Redactor) -> None:
        self._journal = journal
        self._redactor = redactor
        self._lock = Lock()
        self._failed_traces: set[str] = set()

    def on_start(self, span: Any, parent_context: Any = None) -> None:  # noqa: D102
        return None

    def on_end(self, span: ReadableSpan) -> None:
        context = span.get_span_context()
        trace_id = format(context.trace_id, "032x")
        start_ns = span.start_time or 0
        end_ns = span.end_time or start_ns
        try:
            self._journal.record_span(
                trace_id=trace_id,
                span_id=format(context.span_id, "016x"),
                parent_span_id=format(span.parent.span_id, "016x") if span.parent else None,
                name=span.name,
                started_at_utc=datetime.fromtimestamp(start_ns / 1e9, tz=UTC),
                ended_at_utc=datetime.fromtimestamp(end_ns / 1e9, tz=UTC),
                duration_ms=int((end_ns - start_ns) / 1e6),
                status=span.status.status_code.name,
                attributes_json=json.dumps(
                    self._redactor(dict(span.attributes or {})), default=str, sort_keys=True
                ),
            )
        except Exception:
            with self._lock:
                self._failed_traces.add(trace_id)
            logger.exception("failed to persist span %s of trace %s", span.name, trace_id)

    def had_failure(self, trace_id: str) -> bool:
        with self._lock:
            return trace_id in self._failed_traces

    def forget(self, trace_id: str) -> None:
        with self._lock:
            self._failed_traces.discard(trace_id)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


def configure_logging(settings: Settings, redactor: Redactor) -> None:
    """Root logging to stderr (journald captures it), with redaction on every record."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter(redactor))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())


def _parse_otlp_headers(raw: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for chunk in (piece.strip() for piece in (raw or "").split(",")):
        if not chunk:
            continue
        key, separator, value = chunk.partition("=")
        if separator:
            headers[key.strip()] = value.strip()
    return headers


def configure_tracing(
    settings: Settings, journal: Journal, redactor: Redactor
) -> tuple[TracerProvider, JournalSpanProcessor]:
    """Install the tracer provider. The journal sink always runs; OTLP export is optional."""
    from . import __version__

    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "service.version": __version__,
            "deployment.environment.name": settings.environment,
        }
    )
    provider = TracerProvider(resource=resource)
    journal_processor = JournalSpanProcessor(journal, redactor)
    provider.add_span_processor(journal_processor)

    if settings.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        exporter = OTLPSpanExporter(
            endpoint=settings.otlp_endpoint,
            headers=_parse_otlp_headers(
                settings.otlp_headers.get_secret_value() if settings.otlp_headers else None
            ),
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info("OTLP span export enabled to %s", settings.otlp_endpoint)

    trace.set_tracer_provider(provider)
    return provider, journal_processor


def get_tracer() -> Tracer:
    return trace.get_tracer("strike_desk")
