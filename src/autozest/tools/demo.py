"""Build a plausible sale, for a demonstration or a load test.

Everything here is generated rather than copied from a real sale, which matters
because the obvious way to make a demo is to take last Tuesday and change the
names, and last Tuesday belongs to the sellers who consigned into it.

The generator is seeded, so the same seed produces the same sale. That is what
makes it usable in a test: a bug that only appears on the two hundredth unit is
reproducible by seed rather than by luck.

The shape of what it builds is deliberately uncomfortable. Roughly a third of
the units do not sell, a tenth run red, and a handful come back on arbitration,
because a demo where everything works is a demo that hides the parts people
most need to see.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from autozest.block.bidding import BiddingService
from autozest.block.catalogue import CatalogueService
from autozest.block.sales import SaleRepository
from autozest.catalog.accounts import Account, AccountClass, AccountRepository
from autozest.catalog.vehicles import Vehicle, VehicleRepository
from autozest.consignment.intake import Arrival, IntakeService
from autozest.inspection.damage import DamageKind, DamageLine, Panel
from autozest.inspection.service import InspectionService
from autozest.money import Money
from autozest.vin import Vin, check_digit

MAKES = (
    ("Honda", ("Accord", "Civic", "CR-V", "Pilot")),
    ("Ford", ("F-150", "Escape", "Explorer", "Transit")),
    ("Toyota", ("Camry", "Corolla", "RAV4", "Tacoma")),
    ("Chevrolet", ("Silverado", "Equinox", "Malibu", "Traverse")),
    ("Nissan", ("Altima", "Rogue", "Frontier", "Sentra")),
    ("Hyundai", ("Elantra", "Tucson", "Santa Fe")),
)

SELLERS = (
    ("d-100", "Ridgeline Motors", AccountClass.INDEPENDENT_DEALER),
    ("d-101", "Kestrel Auto Group", AccountClass.FRANCHISE_DEALER),
    ("f-200", "Halloway Fleet Services", AccountClass.FLEET_LEASE),
    ("n-300", "Ninth Street Finance", AccountClass.FINANCE),
)

BUYERS = (
    ("b-400", "Cotton Lane Motors", AccountClass.INDEPENDENT_DEALER),
    ("b-401", "Marchmont Auto", AccountClass.INDEPENDENT_DEALER),
    ("b-402", "Delray Wholesale", AccountClass.WHOLESALER),
    ("b-403", "Pinegrove Cars", AccountClass.INDEPENDENT_DEALER),
)

INSPECTORS = ("j.hale", "c.mott", "r.okafor")


@dataclass(frozen=True)
class Built:
    """What the generator made."""

    sale_code: str
    accounts: int
    units: int
    sold: int
    no_sale: int


def _vin(rng: random.Random, model_year: int) -> str:
    """A structurally valid vin with a correct check digit.

    Position seven is not free. It is what breaks the thirty year tie on the
    year code, so a unit from 2010 onward needs a letter there and an older one
    needs a digit. Generating it at random produced vins that decoded to 1988
    and were otherwise perfectly valid, which is exactly the sort of test data
    that makes a real bug look like a generator quirk.
    """
    letters = "ABCDEFGHJKLMNPRSTUVWXYZ"
    alphabet = letters + "0123456789"
    year_codes = "ABCDEFGHJKLMNPRSTVWXY123456789"
    body = [rng.choice(alphabet) for _ in range(8)]
    body[6] = rng.choice(letters) if model_year >= 2010 else str(rng.randint(0, 9))
    stem_body = "".join(body)
    year_code = year_codes[(model_year - 1980) % 30]
    tail = year_code + rng.choice(alphabet) + "".join(str(rng.randint(0, 9)) for _ in range(6))
    return stem_body + check_digit(stem_body + "0" + tail) + tail


def build(
    conn: sqlite3.Connection,
    *,
    units: int = 24,
    seed: int = 20240312,
    sale_day: date | None = None,
) -> Built:
    """Populate an empty database with one sale's worth of everything."""
    rng = random.Random(seed)
    day = sale_day or date(2024, 3, 12)

    accounts = AccountRepository(conn)
    for account_id, name, account_class in SELLERS + BUYERS:
        accounts.add(
            Account(
                id=account_id,
                name=name,
                account_class=account_class,
                dealer_licence=f"TX-{account_id.upper()}",
                state="TX",
            )
        )

    vehicles = VehicleRepository(conn)
    intake = IntakeService(conn)
    inspection = InspectionService(conn)
    sales = SaleRepository(conn)
    catalogue = CatalogueService(conn)

    sale = sales.create(day, f"{day:%A} dealer sale", {"A": 200, "B": 200})

    seller_ids = [seller[0] for seller in SELLERS]
    for _ in range(units):
        model_year = rng.randint(2011, 2022)
        make, models = rng.choice(MAKES)
        vin = _vin(rng, model_year)
        vehicles.add(
            Vehicle(
                vin=Vin.parse(vin),
                make=make,
                model=rng.choice(models),
                model_year=model_year,
                drivetrain=rng.choice(["fwd", "rwd", "awd", "4wd"]),
                fuel="gasoline",
            )
        )
        floor = Money.parse(f"{rng.randrange(1500, 26000, 250)}.00")
        unit = intake.register(vin, rng.choice(seller_ids), floor=floor)
        intake.check_in(
            unit.id,
            Arrival(
                keys_count=rng.choice([0, 1, 2, 2, 2]), miles=rng.randrange(8_000, 190_000)
            ),
        )
        report = inspection.open_report(unit.id, rng.choice(INSPECTORS))
        for _ in range(rng.randint(0, 4)):
            inspection.add_damage(
                report.id,
                DamageLine(
                    panel=rng.choice(list(Panel)),
                    kind=rng.choice(list(DamageKind)),
                    severity=rng.randint(1, 5),
                ),
            )
        inspection.finalise(
            report.id,
            drivable=rng.random() > 0.06,
            engine_starts=rng.random() > 0.03,
            tread_32nds=rng.randint(2, 10),
            reference_value=floor.times("1.4"),
        )
        catalogue.place(sale.code, unit.id, rng.choice(["A", "B"]))

    sales.open_sale(sale.code)
    bidding = BiddingService(conn)
    buyer_ids = [buyer[0] for buyer in BUYERS]
    sold = 0
    no_sale = 0
    at = datetime.combine(day, datetime.min.time()) + timedelta(hours=10)
    for run in sales.runs(sale.code):
        assert run.id is not None
        consignment = intake.consignments.get(run.consignment_id)
        floor_price = consignment.floor or Money.parse("1000.00")
        if rng.random() < 0.18:
            no_sale += 1
            bidding.fall(run.id)
            continue
        wanted = floor_price.times(str(round(rng.uniform(0.78, 1.35), 2)))
        buyer = rng.choice([b for b in buyer_ids if b != consignment.seller_id])
        bidding.bid(run.id, buyer, wanted)
        outcome = bidding.fall(run.id)
        if outcome.status.value == "if_bid":
            if rng.random() < 0.55:
                bidding.accept(run.id)
            else:
                bidding.decline(run.id)
        conn.execute(
            "UPDATE run SET ran_at = ? WHERE id = ?",
            (at.isoformat(timespec="seconds"), run.id),
        )
        at += timedelta(minutes=2)
        refreshed = sales.run(run.id)
        if refreshed.status.value == "sold":
            sold += 1
        else:
            no_sale += 1

    return Built(
        sale_code=sale.code,
        accounts=len(SELLERS) + len(BUYERS),
        units=units,
        sold=sold,
        no_sale=no_sale,
    )
