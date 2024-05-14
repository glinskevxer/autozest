"""Exact money arithmetic.

Every amount in this system is a whole number of cents held in an ``int``.
Floats are never used and never accepted. A hammer price is the anchor for the
buyer fee tier, the seller fee tier and any later price adjustment, so a
half-cent of drift in one place shows up as an unbalanced ledger in another.

Negative amounts are legitimate. A seller statement can net negative when the
fees and the lien payoff exceed the proceeds, and an arbitration adjustment is
recorded as a negative line against the original sale.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from autozest.errors import MoneyError

_CENTS = Decimal("0.01")
_PARSE = re.compile(
    r"""^\s*
        (?P<paren>\()?\s*
        (?P<sign>[-+])?\s*
        \$?\s*
        (?P<whole>\d{1,3}(?:,\d{3})*|\d+)
        (?:\.(?P<frac>\d{1,2}))?
        \s*(?(paren)\))\s*$""",
    re.VERBOSE,
)


@dataclass(frozen=True, order=True)
class Money:
    """An amount in whole cents.

    Instances are immutable and orderable, so they sort and compare the way an
    invoice line is expected to.
    """

    cents: int

    def __post_init__(self) -> None:
        if not isinstance(self.cents, int) or isinstance(self.cents, bool):
            raise MoneyError(f"cents must be an int, got {type(self.cents).__name__}")

    @classmethod
    def zero(cls) -> Money:
        return cls(0)

    @classmethod
    def from_dollars(cls, value: str | int | Decimal) -> Money:
        """Build from a dollar quantity. Anything finer than a cent is rejected."""
        if isinstance(value, float):
            raise MoneyError("floats are not accepted, pass a str or Decimal")
        try:
            dec = Decimal(value)
        except InvalidOperation as exc:
            raise MoneyError(f"not a decimal amount: {value!r}") from exc
        if not dec.is_finite():
            raise MoneyError(f"amount is not finite: {value!r}")
        scaled = dec * 100
        if scaled != scaled.to_integral_value():
            raise MoneyError(f"amount has sub-cent precision: {value!r}")
        return cls(int(scaled))

    @classmethod
    def parse(cls, text: str) -> Money:
        """Parse the shapes that turn up in imported files.

        Accepts ``$12,350.00``, ``12350``, ``-45.10`` and the accounting
        parenthesis form ``(45.10)`` for a negative.
        """
        match = _PARSE.match(text)
        if match is None:
            raise MoneyError(f"cannot parse an amount from {text!r}")
        whole = match.group("whole").replace(",", "")
        frac = (match.group("frac") or "").ljust(2, "0")
        cents = int(whole) * 100 + int(frac)
        if match.group("paren") or match.group("sign") == "-":
            cents = -cents
        return cls(cents)

    @property
    def dollars(self) -> Decimal:
        return (Decimal(self.cents) / 100).quantize(_CENTS)

    def __str__(self) -> str:
        sign = "-" if self.cents < 0 else ""
        whole, frac = divmod(abs(self.cents), 100)
        return f"{sign}{whole:,}.{frac:02d}"

    def __repr__(self) -> str:
        return f"Money.parse('{self}')"

    def __format__(self, spec: str) -> str:
        """Let an amount go straight into an f-string with an alignment.

        Without this ``f"{amount:>12}"`` raises, which is exactly the shape
        every report line wants and exactly the shape nobody tests until the
        report runs. The spec is applied to the rendered text, so the width
        and fill work and a numeric spec such as ``.2f`` is refused rather
        than quietly producing something wrong.
        """
        if spec and spec[-1] in "bcdeEfFgGnosxX%":
            raise MoneyError(f"{spec!r} is a number format, an amount is text here")
        return format(str(self), spec)

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.cents + other.cents)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.cents - other.cents)

    def __neg__(self) -> Money:
        return Money(-self.cents)

    def __abs__(self) -> Money:
        return Money(abs(self.cents))

    def __bool__(self) -> bool:
        return self.cents != 0

    @property
    def is_negative(self) -> bool:
        return self.cents < 0

    def times(self, factor: Decimal | int | str) -> Money:
        """Multiply, rounding a half cent away from zero as the fee tables do."""
        if isinstance(factor, float):
            raise MoneyError("floats are not accepted, pass a str, int or Decimal")
        product = Decimal(self.cents) * Decimal(factor)
        return Money(int(product.quantize(Decimal(1), rounding=ROUND_HALF_UP)))

    def percent(self, pct: Decimal | int | str) -> Money:
        """Take a percentage. ``percent('4.5')`` is four and a half percent."""
        return self.times(Decimal(pct) / 100)

    def clamp(self, low: Money | None = None, high: Money | None = None) -> Money:
        if low is not None and high is not None and low > high:
            raise MoneyError(f"clamp bounds are inverted: {low} > {high}")
        out = self
        if low is not None and out < low:
            out = low
        if high is not None and out > high:
            out = high
        return out

    def allocate(self, weights: Sequence[int]) -> list[Money]:
        """Split across weights so the parts add back to the whole exactly.

        Largest remainder: every part gets the floor of its share, then the
        leftover cents go one each to the parts with the biggest remainders.
        Ties fall to the earlier position, which keeps the result stable for a
        given weight order.
        """
        if not weights:
            raise MoneyError("allocate needs at least one weight")
        if any(w < 0 for w in weights):
            raise MoneyError("allocate does not take negative weights")
        total_weight = sum(weights)
        if total_weight == 0:
            raise MoneyError("allocate needs the weights to sum to more than zero")
        sign = -1 if self.cents < 0 else 1
        pool = abs(self.cents)
        shares = [pool * w // total_weight for w in weights]
        remainders = [(pool * w % total_weight, -index) for index, w in enumerate(weights)]
        leftover = pool - sum(shares)
        order = sorted(range(len(weights)), key=lambda i: remainders[i], reverse=True)
        for i in order[:leftover]:
            shares[i] += 1
        return [Money(sign * s) for s in shares]

    def split(self, parts: int) -> list[Money]:
        """Split into equal parts, the odd cents landing on the earlier parts."""
        if parts < 1:
            raise MoneyError(f"cannot split into {parts} parts")
        return self.allocate([1] * parts)


def total(amounts: Sequence[Money]) -> Money:
    """Sum a sequence of amounts. An empty sequence is zero, not an error."""
    return Money(sum(a.cents for a in amounts))
