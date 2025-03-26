"""Every message this system raises has to read the same way.

Operators read these on a terminal, quote them down a phone and paste them into
tickets. A set of messages where half start with a capital, a third end in a
full stop and one shouts is a set nobody can search.

The house style, arrived at by looking at what was already there and picking
the majority:

* lower case, because the message is usually the tail of "error: ..."
* no full stop, because it is a fragment and not a sentence
* no exclamation mark, ever
* says what is wrong, not what the code was doing
"""

from __future__ import annotations

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src" / "autozest"

#: A message string handed to one of our exceptions.
_MESSAGES = re.compile(
    r"(?:ValidationError|Conflict|PolicyViolation|ConfigError|NotFound|StateError|"
    r"MoneyError|VinError|AutozestError)\(\s*\n?\s*"
    r'(?:"[a-z_.]+",\s*\n?\s*)?'
    r'(?:f?"((?:[^"\\]|\\.)*)")',
    re.MULTILINE,
)

#: Words that legitimately start a message with a capital.
_ALLOWED_CAPITALS = ("SQLite", "TOML", "WAL")


#: An f-string interpolation. Stripped before checking, because a repr inside
#: one carries a bang and a dotted name that are not the message's doing.
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def messages() -> list[tuple[Path, str]]:
    """Every message string handed to one of our exceptions, interpolations out."""
    found: list[tuple[Path, str]] = []
    for path in sorted(SOURCE.rglob("*.py")):
        for match in _MESSAGES.finditer(path.read_text("utf-8")):
            text = _PLACEHOLDER.sub("", match.group(1)).strip()
            if text:
                found.append((path, text))
    return found


def test_there_are_messages_to_check() -> None:
    assert len(messages()) > 80


def test_nothing_starts_with_a_capital() -> None:
    offenders = [
        f"{path.name}: {text}"
        for path, text in messages()
        if text[:1].isupper() and not text.startswith(_ALLOWED_CAPITALS)
    ]
    assert offenders == []


def test_nothing_ends_with_a_full_stop() -> None:
    offenders = [
        f"{path.name}: {text}" for path, text in messages() if text.rstrip().endswith(".")
    ]
    assert offenders == []


def test_nothing_shouts() -> None:
    offenders = [f"{path.name}: {text}" for path, text in messages() if "!" in text]
    assert offenders == []


def test_nothing_is_a_placeholder() -> None:
    """A message saying nothing more than that something went wrong."""
    useless = {"error", "failed", "bad input", "invalid", "wrong"}
    offenders = [f"{path.name}: {text}" for path, text in messages() if text.lower() in useless]
    assert offenders == []


def test_nothing_leaks_a_python_word() -> None:
    """A message naming a class or a traceback is a message for us, not them."""
    leaks = ("NoneType", "traceback", "Traceback", "__main__")
    offenders = [
        f"{path.name}: {text}"
        for path, text in messages()
        if any(word in text for word in leaks)
    ]
    assert offenders == []
