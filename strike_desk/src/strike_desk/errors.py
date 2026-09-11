"""Exception taxonomy — every failure the tick can survive has a type here."""

from __future__ import annotations


class StrikeDeskError(Exception):
    """Base class for every error raised inside Strike Desk."""


class OpenAlgoError(StrikeDeskError):
    """A call to OpenAlgo failed, timed out, or answered with an error status."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ReadOnlyViolation(StrikeDeskError):
    """Tick code attempted a path outside the read-only whitelist."""

    def __init__(self, path: str) -> None:
        super().__init__(f"path {path!r} is not in the read-only whitelist")
        self.path = path


class BookStateUnavailable(StrikeDeskError):
    """The book could not be read or parsed; the desk must not assume it is flat."""


class SpecialistUnavailable(StrikeDeskError):
    """No usable specialist answered for a role."""

    def __init__(self, role: str, detail: str) -> None:
        super().__init__(f"specialist role {role!r} unavailable: {detail}")
        self.role = role
        self.detail = detail


class SpecialistTimeout(StrikeDeskError):
    """A specialist exceeded its timeout budget."""

    def __init__(self, role: str, detail: str) -> None:
        super().__init__(f"specialist role {role!r} timed out: {detail}")
        self.role = role
        self.detail = detail


class JournalWriteError(StrikeDeskError):
    """The append-only journal could not be written; the tick must fail closed."""


class PromptNotFound(StrikeDeskError):
    """A prompt artifact was requested that the registry does not hold."""


class McpUnavailable(StrikeDeskError):
    """The OpenAlgo MCP server could not be started, reached, or trusted."""


class ModelCallFailed(StrikeDeskError):
    """The model provider refused, errored, or was not configured."""


class EventCalendarInvalid(StrikeDeskError):
    """The event calendar exists but cannot be parsed; the desk must not guess."""


class PlaybookViolation(StrikeDeskError):
    """A proposal failed one or more of the playbook's numeric constraints."""


class RiskInputUnavailable(StrikeDeskError):
    """A limit could not be evaluated because an input it needs was unusable."""


class MirrorUnavailable(StrikeDeskError):
    """OpenAlgo's own database could not be read, so the approval gate is unverifiable."""


class ExecutionPathViolation(StrikeDeskError):
    """Execution code attempted a path outside the execution whitelist."""

    def __init__(self, path: str) -> None:
        super().__init__(f"path {path!r} is not in the execution whitelist")
        self.path = path


class InvalidOrderPayload(StrikeDeskError):
    """An order payload failed its own validation and was never sent."""


class UnpriceableBand(StrikeDeskError):
    """The entry band contains no price on the exchange's tick grid."""


class ApprovalGateBypassed(StrikeDeskError):
    """A placement was accepted without the human gate — the desk must stop."""


class AlreadyJournalled(StrikeDeskError):
    """This exact append-only row already exists; the write was a repeat, not a failure."""


class FeedUnavailable(StrikeDeskError):
    """The live price feed could not be reached, authenticated or subscribed."""


class LevelsUnavailable(StrikeDeskError):
    """A position was adopted whose stop, target or time-stop cannot be resolved."""


class ExitPathGated(StrikeDeskError):
    """An exit would be queued for human approval — the desk must not hold this position."""


class ExitFailed(StrikeDeskError):
    """Every rung of the exit ladder failed; the position is still open."""


class AutonomyMismatch(StrikeDeskError):
    """OpenAlgo's order mode contradicts the desk's configured autonomy."""


class MonitorUnavailable(StrikeDeskError):
    """No live position monitor, so no unattended entry may be formed."""


class ExecutionFailed(StrikeDeskError):
    """An unattended submission returned no broker order id."""
