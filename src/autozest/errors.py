"""The exception hierarchy.

Everything the package raises on purpose descends from :class:`AutozestError`,
carries a stable ``code`` and an optional mapping of context. The command line
turns the code into an exit status and prints the context underneath the
message, so a failure in a nightly import is diagnosable from the log alone
without anyone reproducing it.

Codes are short, lower case and never reused for a different meaning. They are
part of the interface: an operator greps for them.
"""

from __future__ import annotations

from typing import Any


class AutozestError(Exception):
    """Base class. Never raised directly."""

    code = "error"
    exit_status = 1

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = dict(context)

    def __str__(self) -> str:
        if not self.context:
            return self.message
        rendered = ", ".join(f"{k}={v!r}" for k, v in sorted(self.context.items()))
        return f"{self.message} ({rendered})"

    def detail(self) -> str:
        """The message and its context on separate lines, for the log."""
        lines = [f"[{self.code}] {self.message}"]
        lines.extend(f"    {k}: {v!r}" for k, v in sorted(self.context.items()))
        return "\n".join(lines)


class ValidationError(AutozestError, ValueError):
    """The input is not a well formed instance of what it claims to be.

    Also a ``ValueError`` so that callers written against the standard library
    keep working, which matters for the import scripts operations still runs.
    """

    code = "validation"
    exit_status = 2


class MoneyError(ValidationError):
    """An amount cannot be represented or parsed."""

    code = "money"


class VinError(ValidationError):
    """A string cannot be a VIN, or it fails its own check digit."""

    code = "vin"


class NotFound(AutozestError):
    """A record was asked for by key and is not there."""

    code = "not_found"
    exit_status = 4

    def __init__(self, kind: str, key: object) -> None:
        super().__init__(f"no {kind} with key {key!r}", kind=kind, key=key)
        self.kind = kind
        self.key = key


class Conflict(AutozestError):
    """The record exists and the operation would duplicate or contradict it."""

    code = "conflict"
    exit_status = 5


class StateError(AutozestError):
    """The record is real but is in the wrong state for this transition."""

    code = "state"
    exit_status = 6

    def __init__(self, subject: str, current: str, wanted: str) -> None:
        super().__init__(
            f"{subject} is {current}, this needs {wanted}",
            subject=subject,
            current=current,
            wanted=wanted,
        )
        self.current = current
        self.wanted = wanted


class PolicyViolation(AutozestError):
    """The operation is well formed but a policy rule forbids it.

    This is the one an operator sees most. It always names the rule so the
    answer to "why can I not do this" is in the message rather than in a
    handbook nobody has open.
    """

    code = "policy"
    exit_status = 7

    def __init__(self, rule: str, message: str, **context: Any) -> None:
        super().__init__(message, rule=rule, **context)
        self.rule = rule


class ConfigError(AutozestError):
    """The policy configuration is missing, unreadable or internally wrong."""

    code = "config"
    exit_status = 3


def exit_status_for(exc: BaseException) -> int:
    """Map any exception to a process exit status."""
    if isinstance(exc, AutozestError):
        return exc.exit_status
    return 1
