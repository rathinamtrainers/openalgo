"""The decline report: what the desk decided, counted and classified from the journal."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .config import IST
from .decline_taxonomy import (
    CATEGORY_UNKNOWN,
    DISPOSITION_DEFECT,
    TAXONOMY_ARTIFACT,
    TAXONOMY_DIGEST,
    TAXONOMY_VERSION,
    describe,
)
from .journal import Journal


def _to_ist(value: datetime | None) -> str | None:
    """Journal timestamps are UTC; SQLite may hand them back naive."""
    if value is None:
        return None
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return moment.astimezone(IST).strftime("%H:%M:%S")


@dataclass(frozen=True)
class ReasonRow:
    """One reason code as it was recorded on one day."""

    code: str
    outcome: str
    category: str
    disposition: str
    summary: str
    count: int
    unstamped: int
    drift: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason_code": self.code,
            "outcome": self.outcome,
            "category": self.category,
            "disposition": self.disposition,
            "summary": self.summary,
            "count": self.count,
            "unstamped": self.unstamped,
            "drift": self.drift,
        }


@dataclass(frozen=True)
class DayReport:
    """One IST trading day, counted from the append-only journal."""

    trading_day: str
    index_symbol: str
    total: int
    by_outcome: dict[str, int]
    reasons: tuple[ReasonRow, ...]
    by_category: dict[str, int]
    by_disposition: dict[str, int]
    regime_labels: dict[str, int]
    first_at_ist: str | None
    last_at_ist: str | None
    token_cost_micros: int
    trace_incomplete: int
    unstamped: int
    drift: int
    unknown_codes: tuple[str, ...]

    @property
    def declines(self) -> int:
        return self.by_outcome.get("decline", 0)

    @property
    def holds(self) -> int:
        return self.by_outcome.get("hold", 0)

    @property
    def entries(self) -> int:
        return self.by_outcome.get("enter", 0)

    @property
    def no_trade(self) -> int:
        return self.declines + self.holds

    @property
    def defects(self) -> int:
        return self.by_disposition.get(DISPOSITION_DEFECT, 0)

    @property
    def healthy(self) -> bool:
        """A day is clean when nothing in it was a defect or an unrecognised code."""
        return self.defects == 0 and not self.unknown_codes

    def as_dict(self) -> dict[str, Any]:
        return {
            "trading_day": self.trading_day,
            "index_symbol": self.index_symbol,
            "total": self.total,
            "by_outcome": dict(self.by_outcome),
            "reasons": [row.as_dict() for row in self.reasons],
            "by_category": dict(self.by_category),
            "by_disposition": dict(self.by_disposition),
            "regime_labels": dict(self.regime_labels),
            "first_at_ist": self.first_at_ist,
            "last_at_ist": self.last_at_ist,
            "token_cost_micros": self.token_cost_micros,
            "trace_incomplete": self.trace_incomplete,
            "unstamped": self.unstamped,
            "drift": self.drift,
            "unknown_codes": list(self.unknown_codes),
        }


@dataclass(frozen=True)
class WindowReport:
    """Several trading days, with the same counts aggregated across them."""

    days: tuple[DayReport, ...]
    index_symbol: str

    @property
    def total(self) -> int:
        return sum(day.total for day in self.days)

    @property
    def by_outcome(self) -> dict[str, int]:
        return _merge(day.by_outcome for day in self.days)

    @property
    def by_category(self) -> dict[str, int]:
        return _merge(day.by_category for day in self.days)

    @property
    def by_disposition(self) -> dict[str, int]:
        return _merge(day.by_disposition for day in self.days)

    @property
    def by_reason(self) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for day in self.days:
            for row in day.reasons:
                counter[row.code] += row.count
        return dict(counter.most_common())

    @property
    def token_cost_micros(self) -> int:
        return sum(day.token_cost_micros for day in self.days)

    @property
    def defects(self) -> int:
        return sum(day.defects for day in self.days)

    @property
    def unknown_codes(self) -> tuple[str, ...]:
        return tuple(sorted({code for day in self.days for code in day.unknown_codes}))

    @property
    def healthy(self) -> bool:
        return all(day.healthy for day in self.days)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index_symbol": self.index_symbol,
            "days": [day.as_dict() for day in self.days],
            "total": self.total,
            "by_outcome": self.by_outcome,
            "by_reason": self.by_reason,
            "by_category": self.by_category,
            "by_disposition": self.by_disposition,
            "token_cost_micros": self.token_cost_micros,
            "defects": self.defects,
            "unknown_codes": list(self.unknown_codes),
        }


def _merge(mappings: Any) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for mapping in mappings:
        counter.update(mapping)
    return dict(counter.most_common())


def build_day_report(journal: Journal, trading_day: str, index_symbol: str) -> DayReport:
    """Count one day. Categories are resolved from the taxonomy, not trusted from the row."""
    per_code: dict[str, dict[str, Any]] = {}
    by_outcome: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    by_disposition: Counter[str] = Counter()
    unstamped = 0
    drift = 0

    for group in journal.decision_rollup(trading_day):
        code = group["reason_code"]
        count = group["count"]
        entry = describe(code)
        bucket = per_code.setdefault(
            code,
            {
                "outcome": group["outcome"],
                "count": 0,
                "unstamped": 0,
                "drift": 0,
            },
        )
        bucket["count"] += count
        stored = group["reason_category"]
        if stored is None:
            bucket["unstamped"] += count
            unstamped += count
        elif stored != entry.category:
            bucket["drift"] += count
            drift += count
        by_outcome[group["outcome"]] += count
        by_category[entry.category] += count
        by_disposition[entry.disposition] += count

    reasons = tuple(
        ReasonRow(
            code=code,
            outcome=bucket["outcome"],
            category=describe(code).category,
            disposition=describe(code).disposition,
            summary=describe(code).summary,
            count=bucket["count"],
            unstamped=bucket["unstamped"],
            drift=bucket["drift"],
        )
        for code, bucket in sorted(per_code.items(), key=lambda item: (-item[1]["count"], item[0]))
    )
    first, last = journal.decision_bounds(trading_day)
    return DayReport(
        trading_day=trading_day,
        index_symbol=index_symbol,
        total=sum(by_outcome.values()),
        by_outcome=dict(by_outcome),
        reasons=reasons,
        by_category=dict(by_category.most_common()),
        by_disposition=dict(by_disposition.most_common()),
        regime_labels=journal.regime_label_rollup(trading_day),
        first_at_ist=_to_ist(first),
        last_at_ist=_to_ist(last),
        token_cost_micros=journal.decision_cost_micros(trading_day),
        trace_incomplete=journal.incomplete_trace_count(trading_day),
        unstamped=unstamped,
        drift=drift,
        unknown_codes=tuple(
            sorted(row.code for row in reasons if row.category == CATEGORY_UNKNOWN)
        ),
    )


def build_window_report(
    journal: Journal, index_symbol: str, days: Sequence[str] | None = None, limit: int = 5
) -> WindowReport:
    """Count the given days, or the most recent ``limit`` days the journal holds."""
    chosen = list(days) if days is not None else journal.recent_trading_days(limit)
    return WindowReport(
        days=tuple(build_day_report(journal, day, index_symbol) for day in sorted(chosen)),
        index_symbol=index_symbol,
    )


def _share(count: int, total: int) -> str:
    return f"{(100.0 * count / total):5.1f}%" if total else "    -"


def _block(title: str, counts: dict[str, int], total: int) -> list[str]:
    if not counts:
        return []
    lines = [f"{title}"]
    for name, count in counts.items():
        lines.append(f"  {name:<26} {count:>5}  {_share(count, total)}")
    lines.append("")
    return lines


def render_day(report: DayReport) -> str:
    """The day, as the trader reads it in a terminal."""
    lines = [
        f"decline report - {report.trading_day}  ({report.index_symbol})",
        f"  decisions        : {report.total}"
        f"   (declines {report.declines} | holds {report.holds} | entries {report.entries})",
        f"  first / last     : {report.first_at_ist or '-'} to {report.last_at_ist or '-'} IST",
        f"  token cost       : ${report.token_cost_micros / 1_000_000:.4f}",
        f"  taxonomy         : {TAXONOMY_ARTIFACT}",
        "",
    ]
    lines += _block("by disposition", report.by_disposition, report.total)
    lines += _block("by category", report.by_category, report.total)
    if report.reasons:
        lines.append("by reason")
        for row in report.reasons:
            lines.append(
                f"  {row.code:<26} {row.count:>5}  {_share(row.count, report.total)}  "
                f"{row.outcome:<8} {row.category}/{row.disposition} - {row.summary}"
            )
        lines.append("")
    lines += _block("regime labels read", report.regime_labels, sum(report.regime_labels.values()))
    lines += [
        "record health",
        f"  unstamped rows             {report.unstamped:>5}",
        f"  taxonomy drift             {report.drift:>5}",
        f"  incomplete traces          {report.trace_incomplete:>5}",
        f"  unknown reason codes       {', '.join(report.unknown_codes) or 'none':>5}",
    ]
    return "\n".join(lines)


def render_window(window: WindowReport) -> str:
    """Several days: one line each, then the same breakdowns over the whole window."""
    if not window.days:
        return "decline report - no decisions in the journal for the requested window"
    lines = [
        f"decline report - {window.days[0].trading_day} to {window.days[-1].trading_day}"
        f"  ({window.index_symbol}, {len(window.days)} day(s))",
        "",
        "per day",
        f"  {'day':<12}{'total':>6}{'declines':>10}{'holds':>7}{'entries':>9}"
        f"{'defects':>9}{'cost':>10}",
    ]
    for day in window.days:
        lines.append(
            f"  {day.trading_day:<12}{day.total:>6}{day.declines:>10}{day.holds:>7}"
            f"{day.entries:>9}{day.defects:>9}{day.token_cost_micros / 1_000_000:>10.4f}"
        )
    lines.append("")
    lines += _block("by disposition", window.by_disposition, window.total)
    lines += _block("by category", window.by_category, window.total)
    lines += _block("by reason", window.by_reason, window.total)
    lines += [
        f"window total     : {window.total} decisions, ${window.token_cost_micros / 1_000_000:.4f}",
        f"taxonomy         : {TAXONOMY_ARTIFACT}",
        f"unknown codes    : {', '.join(window.unknown_codes) or 'none'}",
    ]
    return "\n".join(lines)


def to_json(report: DayReport | WindowReport) -> str:
    """The same numbers, for a script rather than a human."""
    payload = {
        "taxonomy_version": TAXONOMY_VERSION,
        "taxonomy_digest": TAXONOMY_DIGEST,
        "report": report.as_dict(),
    }
    return json.dumps(payload, indent=2, sort_keys=True)
