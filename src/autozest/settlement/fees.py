"""What the auction charges, and on what.

Both sides pay. The buyer pays a fee on top of the hammer and the seller pays
one out of it, and neither is a percentage: they are stepped bands, published
on a card that goes out every January. A band is a flat amount for everything
inside it, which is why a unit at nine hundred and ninety nine dollars and a
unit at four hundred pay the same fee and a unit a dollar higher pays more.

Everything else is a service, charged once, at a published price. Services are
kept apart from the sale fee on purpose. A seller reading a statement wants to
see the fee they expected and then a list of things they asked for, not one
number they have to reverse engineer.

The band is chosen on the **effective** hammer, which is the price after any
arbitration adjustment. That is the whole reason an adjustment is more work
than an allowance: it can move a unit into a different band, and then the fee
that was already invoiced is wrong.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from autozest.errors import ConfigError, ValidationError
from autozest.money import Money

#: Bumped when the card changes, and printed on every invoice.
SCHEDULE_VERSION = "2024.1"


@dataclass(frozen=True)
class FeeBand:
    """One row of the published card.

    ``upper`` is the top of the band and is inclusive. The last band has no
    upper bound and carries ``None``.
    """

    upper: Money | None
    buyer: Money
    seller: Money

    def holds(self, amount: Money) -> bool:
        return self.upper is None or amount <= self.upper

    def __str__(self) -> str:
        top = "and up" if self.upper is None else f"to {self.upper}"
        return f"{top}: buyer {self.buyer}, seller {self.seller}"


def _band(upper: str | None, buyer: str, seller: str) -> FeeBand:
    return FeeBand(
        upper=Money.parse(upper) if upper is not None else None,
        buyer=Money.parse(buyer),
        seller=Money.parse(seller),
    )


DEFAULT_BANDS: tuple[FeeBand, ...] = (
    _band("999.99", "75.00", "60.00"),
    _band("2499.99", "125.00", "95.00"),
    _band("4999.99", "210.00", "150.00"),
    _band("7499.99", "285.00", "195.00"),
    _band("9999.99", "345.00", "235.00"),
    _band("14999.99", "425.00", "290.00"),
    _band("19999.99", "510.00", "345.00"),
    _band("29999.99", "610.00", "410.00"),
    _band("49999.99", "720.00", "480.00"),
    _band(None, "850.00", "560.00"),
)


class Service(Enum):
    """Things charged once, at a published price."""

    GATE = "gate"
    TRANSPORT = "transport"
    DETAIL = "detail"
    POST_SALE_INSPECTION = "post_sale_inspection"
    TITLE_PROCESSING = "title_processing"
    LATE_PAYMENT = "late_payment"
    NO_SALE = "no_sale"
    RERUN = "rerun"
    STORAGE_PER_DAY = "storage_per_day"
    FLOOR_PLAN_PAYOFF = "floor_plan_payoff"

    @property
    def falls_on_buyer(self) -> bool:
        return self in _BUYER_SERVICES


_BUYER_SERVICES = frozenset(
    {
        Service.POST_SALE_INSPECTION,
        Service.LATE_PAYMENT,
        Service.TITLE_PROCESSING,
        Service.TRANSPORT,
    }
)

DEFAULT_SERVICES: dict[Service, Money] = {
    Service.GATE: Money.parse("25.00"),
    Service.TRANSPORT: Money.parse("185.00"),
    Service.DETAIL: Money.parse("95.00"),
    Service.POST_SALE_INSPECTION: Money.parse("140.00"),
    Service.TITLE_PROCESSING: Money.parse("45.00"),
    Service.LATE_PAYMENT: Money.parse("100.00"),
    Service.NO_SALE: Money.parse("110.00"),
    Service.RERUN: Money.parse("55.00"),
    Service.STORAGE_PER_DAY: Money.parse("18.00"),
    Service.FLOOR_PLAN_PAYOFF: Money.parse("35.00"),
}


@dataclass(frozen=True)
class FeeSchedule:
    """One year's card."""

    version: str = SCHEDULE_VERSION
    bands: Sequence[FeeBand] = DEFAULT_BANDS
    services: Mapping[Service, Money] = field(default_factory=lambda: dict(DEFAULT_SERVICES))

    def __post_init__(self) -> None:
        if not self.bands:
            raise ConfigError("a fee schedule needs at least one band")
        if self.bands[-1].upper is not None:
            raise ConfigError("the last fee band has to be open ended")
        tops = [band.upper for band in self.bands[:-1]]
        if any(top is None for top in tops):
            raise ConfigError("only the last fee band may be open ended")
        if tops != sorted(tops, key=lambda m: m.cents if m else 0):
            raise ConfigError("the fee bands are out of order")

    def band_for(self, amount: Money) -> FeeBand:
        """The band an amount falls in."""
        if amount.is_negative:
            raise ValidationError("a fee cannot be charged on a negative amount")
        for band in self.bands:
            if band.holds(amount):
                return band
        return self.bands[-1]

    def buyer_fee(self, amount: Money) -> Money:
        return self.band_for(amount).buyer

    def seller_fee(self, amount: Money) -> Money:
        return self.band_for(amount).seller

    def service(self, service: Service) -> Money:
        try:
            return self.services[service]
        except KeyError as exc:
            raise ConfigError(
                f"the {self.version} card has no price for {service.value}"
            ) from exc

    def total_fees(self, amount: Money) -> Money:
        """Both sides together, which is what the auction earns on a sale."""
        return self.buyer_fee(amount) + self.seller_fee(amount)

    def band_changed(self, before: Money, after: Money) -> bool:
        """Whether moving a price from one figure to another re-tiers the fee."""
        return self.band_for(before) is not self.band_for(after)


def from_config(section: Mapping[str, Any], base: FeeSchedule | None = None) -> FeeSchedule:
    """Overlay a configuration section onto the shipped card."""
    schedule = base or FeeSchedule()
    version = str(section.get("version", schedule.version))
    services = dict(schedule.services)
    bands = list(schedule.bands)

    raw_bands = section.get("bands")
    if raw_bands is not None:
        if not isinstance(raw_bands, list):
            raise ConfigError("[fees] bands should be a list of tables")
        bands = [_band_from(row) for row in raw_bands]

    for key, value in section.items():
        if key in ("version", "bands"):
            continue
        try:
            service = Service(key)
        except ValueError as exc:
            raise ConfigError(
                f"{key!r} is not a service on the card", known=[s.value for s in Service]
            ) from exc
        services[service] = Money.parse(str(value))

    return FeeSchedule(version=version, bands=tuple(bands), services=services)


def _band_from(row: Any) -> FeeBand:
    if not isinstance(row, dict):
        raise ConfigError("every fee band should be a table")
    missing = {"buyer", "seller"} - set(row)
    if missing:
        raise ConfigError(f"a fee band is missing {', '.join(sorted(missing))}")
    upper = row.get("upper")
    return FeeBand(
        upper=Money.parse(str(upper)) if upper is not None else None,
        buyer=Money.parse(str(row["buyer"])),
        seller=Money.parse(str(row["seller"])),
    )
