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
