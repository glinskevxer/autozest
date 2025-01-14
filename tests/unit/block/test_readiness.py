from autozest.block.readiness import check, render
from autozest.block.roster import Role, RosterService, Staff
from tests.conftest import Floor


def signed(floor: Floor, run_id: int) -> None:
    """Give a catalogued unit a signed report.

    The shared floor fixture moves a unit to catalogued without going through
    the inspection floor, which is what most tests want and is exactly what
    this report is looking for.
    """
    consignment_id = floor.sales.run(run_id).consignment_id
    floor.conn.execute(
        "INSERT INTO condition_report (id, consignment_id, inspector, inspected_at, "
        "grade, finalised_at) VALUES (?, ?, 'j.hale', ?, 4.0, ?)",
        (
            f"R-{run_id}",
            consignment_id,
            "2024-03-11T09:00:00+00:00",
            "2024-03-11T10:00:00+00:00",
        ),
    )


def crewed(floor: Floor) -> None:
    roster = RosterService(floor.conn)
    roster.add(Staff(id="s-1", name="Della Voss", role=Role.AUCTIONEER))
    roster.add(Staff(id="s-2", name="Ray Okafor", role=Role.RING))
    roster.add(Staff(id="s-3", name="Mel Byrne", role=Role.AUCTIONEER))
    roster.add(Staff(id="s-4", name="Jo Hale", role=Role.RING))
    roster.assign(floor.code, "A", "s-1")
    roster.assign(floor.code, "A", "s-2")
    roster.assign(floor.code, "B", "s-3")
    roster.assign(floor.code, "B", "s-4")


def test_an_empty_sale_is_not_ready(floor: Floor) -> None:
    report = check(floor.conn, floor.code)
    assert not report.can_open
    names = [item.name for item in report.blockers]
    assert "catalogue" in names
    assert "must be done first" in report.summary()


def test_a_sale_short_of_staff_is_not_ready(floor: Floor) -> None:
    signed(floor, floor.catalogue(floor_price="3000.00"))
    report = check(floor.conn, floor.code)
    assert any(item.name.startswith("lane") for item in report.blockers)


def test_a_unit_with_no_signed_report_blocks(floor: Floor) -> None:
    floor.catalogue(floor_price="3000.00")
    crewed(floor)
    report = check(floor.conn, floor.code)
    assert not report.can_open
    assert any(item.name == "condition reports" for item in report.blockers)


def test_a_catalogued_and_crewed_sale_is_ready(floor: Floor) -> None:
    signed(floor, floor.catalogue(floor_price="3000.00"))
    crewed(floor)
    report = check(floor.conn, floor.code)
    assert report.can_open
    assert report.catalogued == 1
    assert "ready" in report.summary()


def test_the_items_are_ordered_by_how_long_they_take(floor: Floor) -> None:
    floor.catalogue(floor_price="3000.00")
    report = check(floor.conn, floor.code)
    orders = [item.order for item in report.items]
    assert orders == sorted(orders)


def test_a_missing_title_is_a_note_and_not_a_blocker(floor: Floor) -> None:
    signed(floor, floor.catalogue(floor_price="3000.00"))
    crewed(floor)
    report = check(floor.conn, floor.code)
    notes = [item for item in report.items if not item.blocking]
    assert any("title" in item.name for item in notes)
    assert report.can_open


def test_a_sale_already_open_says_so(floor: Floor) -> None:
    signed(floor, floor.catalogue(floor_price="3000.00"))
    crewed(floor)
    floor.sales.open_sale(floor.code)
    report = check(floor.conn, floor.code)
    assert any(item.name == "sale" for item in report.items)
    assert report.can_open


def test_a_blocked_badge_is_noted(floor: Floor) -> None:
    from autozest.block.registration import RegistrationService
    from autozest.catalog.accounts import AccountRepository, AccountStatus

    signed(floor, floor.catalogue(floor_price="3000.00"))
    crewed(floor)
    AccountRepository(floor.conn).set_status("d-200", AccountStatus.SUSPENDED)
    RegistrationService(floor.conn).register(floor.code, "d-200", force=True)
    report = check(floor.conn, floor.code)
    assert any(item.name == "door list" for item in report.items)
    assert report.can_open


def test_the_rendered_clipboard_reads(floor: Floor) -> None:
    floor.catalogue(floor_price="3000.00")
    printed = "\n".join(render(floor.conn, floor.code))
    assert "readiness" in printed
    assert "MUST" in printed
