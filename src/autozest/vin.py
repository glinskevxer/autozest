"""Vehicle identification numbers.

A consignment is keyed on the VIN and nothing else, so a transposed character
at intake follows the unit all the way through to the title. The check digit
catches most of it, which is why every entry point runs through :func:`parse`
rather than storing whatever the gate typed.

North American VINs carry a check digit in position nine. The rest of the world
is not required to and frequently does not, so a failing check digit is a
warning for an import and a hard error for a domestic unit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

VIN_LENGTH = 17
CHECK_POSITION = 9
YEAR_POSITION = 10
PLANT_POSITION = 11

# I, O and Q are excluded from every position so they cannot be confused with
# 1 and 0 on a windscreen plate.
_ALLOWED = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_STRIP = re.compile(r"[\s\-]+")

_TRANSLITERATION = {
    **{str(d): d for d in range(10)},
    "A": 1,
    "B": 2,
    "C": 3,
    "D": 4,
    "E": 5,
    "F": 6,
    "G": 7,
    "H": 8,
    "J": 1,
    "K": 2,
    "L": 3,
    "M": 4,
    "N": 5,
    "P": 7,
    "R": 9,
    "S": 2,
    "T": 3,
    "U": 4,
    "V": 5,
    "W": 6,
    "X": 7,
    "Y": 8,
    "Z": 9,
}
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)

# The year code cycles every thirty values. Position seven breaks the tie: a
# letter there means the later half of the cycle, a digit the earlier half.
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"
_CYCLE_START = 1980

_REGIONS = (
    ("ABCDEFGH", "Africa"),
    ("JKLMNPR", "Asia"),
    ("STUVWXYZ", "Europe"),
    ("12345", "North America"),
    ("67", "Oceania"),
    ("89", "South America"),
)


class VinError(ValueError):
    """Raised when a string cannot be a VIN, or fails its own check digit."""


def normalise(text: str) -> str:
    """Upper case, and drop the spaces and dashes people type into a gate pass."""
    return _STRIP.sub("", text).upper()


def check_digit(vin: str) -> str:
    """Compute the position nine character for an already well formed VIN."""
    if not _ALLOWED.match(vin):
        raise VinError(f"cannot check a malformed vin: {vin!r}")
    running = sum(_TRANSLITERATION[c] * w for c, w in zip(vin, _WEIGHTS, strict=True))
    remainder = running % 11
    return "X" if remainder == 10 else str(remainder)


def decode_model_year(year_code: str, position_seven: str) -> int:
    """Resolve a year code to a calendar year.

    The code repeats every thirty years, so position seven is consulted. A
    numeric seventh character means the 1980 to 2009 half of the cycle and a
    letter means 2010 onward.
    """
    index = _YEAR_CODES.find(year_code.upper())
    if index < 0:
        raise VinError(f"{year_code!r} is not a model year code")
    year = _CYCLE_START + index
    if position_seven.isalpha():
        year += 30
    return year


def wmi_region(wmi: str) -> str:
    """Name the assembly region from the first character of the WMI."""
    first = wmi[:1].upper()
    for chars, name in _REGIONS:
        if first in chars:
            return name
    raise VinError(f"{wmi!r} has no assigned region")


@dataclass(frozen=True)
class Vin:
    """A parsed VIN, split the way the title and the block both refer to it."""

    value: str

    @property
    def wmi(self) -> str:
        return self.value[0:3]

    @property
    def vds(self) -> str:
        return self.value[3:8]

    @property
    def check(self) -> str:
        return self.value[CHECK_POSITION - 1]

    @property
    def year_code(self) -> str:
        return self.value[YEAR_POSITION - 1]

    @property
    def plant(self) -> str:
        return self.value[PLANT_POSITION - 1]

    @property
    def serial(self) -> str:
        return self.value[11:]

    @property
    def model_year(self) -> int:
        return decode_model_year(self.year_code, self.value[6])

    @property
    def region(self) -> str:
        return wmi_region(self.wmi)

    @property
    def check_digit_matches(self) -> bool:
        return self.check == check_digit(self.value)

    @property
    def last_eight(self) -> str:
        """The tail the block announcer reads out and the lot cards carry."""
        return self.value[-8:]

    def masked(self) -> str:
        """Display form for anything a buyer sees before the sale is final."""
        return f"{'*' * 9}{self.last_eight}"

    def __str__(self) -> str:
        return self.value

    @classmethod
    def parse(cls, text: str, *, require_check_digit: bool = True) -> Vin:
        """Normalise, validate the shape, and unless told otherwise the digit."""
        value = normalise(text)
        if len(value) != VIN_LENGTH:
            raise VinError(f"a vin is {VIN_LENGTH} characters, {value!r} is {len(value)}")
        if not _ALLOWED.match(value):
            bad = sorted({c for c in value if not _ALLOWED.match(c * VIN_LENGTH)})
            raise VinError(f"{value!r} contains characters a vin cannot hold: {''.join(bad)}")
        vin = cls(value)
        if require_check_digit and not vin.check_digit_matches:
            raise VinError(
                f"{value!r} fails its check digit, position nine should be "
                f"{check_digit(value)!r} not {vin.check!r}"
            )
        return vin


def is_valid(text: str) -> bool:
    """True when the text parses including its check digit."""
    try:
        Vin.parse(text)
    except VinError:
        return False
    return True
