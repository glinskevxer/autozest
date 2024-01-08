"""The reference numbers that get printed, read out and typed back in.

Every one of these ends up on paper and gets read over a phone, so they are
built to survive that. No character that can be confused with another, a
checksum on the ones a buyer quotes back to us, and a shape that says what kind
of thing it is without anyone having to look it up.
"""

from __future__ import annotations

import re
from datetime import date

from autozest.errors import ValidationError

#: I, O, S and Z are out. They come back as 1, 0, 5 and 2 over a phone.
ALPHABET = "ABCDEFGHJKLMNPQRTUVWXY0123456789"

CONSIGNMENT_PREFIX = "C"
SALE_PREFIX = "S"
CLAIM_PREFIX = "A"
INVOICE_PREFIX = "I"

_SEQUENCED = re.compile(r"^(?P<prefix>[A-Z])-(?P<year>\d{4})-(?P<sequence>\d{6})$")


def sequenced(prefix: str, when: date, sequence: int) -> str:
    """A reference of the form ``C-2024-000412``.

    The year is in it because the sequence restarts every January and the
    filing cabinets are per year.
    """
    if len(prefix) != 1 or not prefix.isalpha() or not prefix.isupper():
        raise ValidationError(f"{prefix!r} is not a one letter upper case prefix")
    if not 0 < sequence < 1_000_000:
        raise ValidationError(f"{sequence} is outside the six digit sequence")
    return f"{prefix}-{when.year:04d}-{sequence:06d}"


def parse_sequenced(reference: str) -> tuple[str, int, int]:
    """Pull the prefix, year and sequence back out of a reference."""
    match = _SEQUENCED.match(reference.strip().upper())
    if match is None:
        raise ValidationError(f"{reference!r} is not a reference of ours")
    return (
        match.group("prefix"),
        int(match.group("year")),
        int(match.group("sequence")),
    )


def consignment_id(when: date, sequence: int) -> str:
    return sequenced(CONSIGNMENT_PREFIX, when, sequence)


def claim_id(when: date, sequence: int) -> str:
    return sequenced(CLAIM_PREFIX, when, sequence)


def invoice_id(when: date, sequence: int) -> str:
    return sequenced(INVOICE_PREFIX, when, sequence)


def sale_code(when: date, lane_count: int) -> str:
    """A sale is named for the day it runs, ``S-20240312``, plus its size."""
    if lane_count < 1:
        raise ValidationError("a sale with no lanes is not a sale")
    return f"{SALE_PREFIX}-{when:%Y%m%d}"


def gate_pass(sequence: int) -> str:
    """A short pass the gate writes on a windscreen. Five characters."""
    if not 0 <= sequence < len(ALPHABET) ** 5:
        raise ValidationError(f"{sequence} does not fit a gate pass")
    digits = []
    remaining = sequence
    for _ in range(5):
        remaining, index = divmod(remaining, len(ALPHABET))
        digits.append(ALPHABET[index])
    return "".join(reversed(digits))


def gate_pass_sequence(pass_text: str) -> int:
    """Turn a gate pass back into the number behind it."""
    text = pass_text.strip().upper()
    if len(text) != 5:
        raise ValidationError(f"{pass_text!r} is not five characters")
    value = 0
    for character in text:
        index = ALPHABET.find(character)
        if index < 0:
            raise ValidationError(f"{character!r} is not in the gate pass alphabet")
        value = value * len(ALPHABET) + index
    return value


def run_number(lane: str, position: int) -> str:
    """A run is a lane letter and a position, ``C214``."""
    if len(lane) != 1 or not lane.isalpha():
        raise ValidationError(f"{lane!r} is not a lane letter")
    if not 0 < position < 1000:
        raise ValidationError(f"{position} is not a run position")
    return f"{lane.upper()}{position:03d}"


def parse_run_number(text: str) -> tuple[str, int]:
    cleaned = text.strip().upper()
    if len(cleaned) != 4 or not cleaned[0].isalpha() or not cleaned[1:].isdigit():
        raise ValidationError(f"{text!r} is not a run number")
    return cleaned[0], int(cleaned[1:])
