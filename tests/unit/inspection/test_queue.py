from datetime import datetime, timedelta

import pytest

from autozest.consignment.intake import Arrival
from autozest.inspection.queue import InspectionQueue, Urgency
from tests.conftest import VINS, Floor


@pytest.fixture()
def queue(floor: Floor) -> InspectionQueue:
    return InspectionQueue(floor.conn)


def arrived(floor: Floor, index: int) -> str:
    unit = floor.intake.register(VINS[index], "d-100")
    floor.intake.check_in(unit.id, Arrival(keys_count=2, miles=40_000))
    return unit.id


def test_nothing_on_the_ground(queue: InspectionQueue) -> None:
    built = queue.build()
    assert built.count == 0
    assert "0 waiting" in built.summary()


def test_an_arrived_unit_is_waiting(floor: Floor, queue: InspectionQueue) -> None:
    arrived(floor, 0)
    built = queue.build()
    assert built.count == 1
    assert built.waiting[0].urgency is Urgency.WAITING


def test_a_unit_with_an_open_report_is_half_done(floor: Floor, queue: InspectionQueue) -> None:
    consignment_id = arrived(floor, 0)
    queue.inspection.open_report(consignment_id, "j.hale")
    built = queue.build()
    assert built.waiting[0].urgency is Urgency.UNFINISHED
    assert built.waiting[0].report is not None


def test_a_unit_already_in_a_sale_comes_first(floor: Floor, queue: InspectionQueue) -> None:
    arrived(floor, 0)
    second = arrived(floor, 1)
    floor.conn.execute(
        "INSERT INTO run (sale_code, lane, position, consignment_id, light, status) "
        "VALUES (?, 'A', 1, ?, 'green', 'scheduled')",
        (floor.code, second),
    )
    built = queue.build()
    assert built.waiting[0].consignment.id == second
    assert built.waiting[0].urgency is Urgency.IN_A_SALE
    assert built.waiting[0].sale_code == floor.code


def test_within_a_group_the_longest_wait_comes_first(
    floor: Floor, queue: InspectionQueue
) -> None:
    first = arrived(floor, 0)
    arrived(floor, 1)
    floor.conn.execute(
        "UPDATE consignment SET arrived_at = ? WHERE id = ?",
        ("2024-01-02T09:00:00+00:00", first),
    )
    built = queue.build(as_at=datetime(2024, 3, 12, 9, 0).astimezone())
    assert built.waiting[0].consignment.id == first
    assert built.waiting[0].days_waiting > built.waiting[1].days_waiting


def test_a_long_wait_is_stale(floor: Floor, queue: InspectionQueue) -> None:
    consignment_id = arrived(floor, 0)
    floor.conn.execute(
        "UPDATE consignment SET arrived_at = ? WHERE id = ?",
        ("2024-01-02T09:00:00+00:00", consignment_id),
    )
    built = queue.build(as_at=datetime(2024, 3, 12, 9, 0).astimezone())
    assert built.waiting[0].is_stale
    assert len(built.stale) == 1


def test_a_signed_report_takes_the_unit_off_the_queue(
    floor: Floor, queue: InspectionQueue
) -> None:
    consignment_id = arrived(floor, 0)
    report = queue.inspection.open_report(consignment_id, "j.hale")
    queue.inspection.finalise(report.id)
    assert queue.build().count == 0


def test_next_up_is_the_top_of_the_list(floor: Floor, queue: InspectionQueue) -> None:
    arrived(floor, 0)
    arrived(floor, 1)
    assert len(queue.next_up(1)) == 1
    assert len(queue.next_up()) == 2


def test_the_rendered_queue_reads(floor: Floor, queue: InspectionQueue) -> None:
    arrived(floor, 0)
    printed = "\n".join(queue.render())
    assert "inspection queue" in printed
    assert "1 waiting" in printed
    assert VINS[0] in printed


def test_a_queue_asked_about_the_past_is_not_negative(
    floor: Floor, queue: InspectionQueue
) -> None:
    arrived(floor, 0)
    earlier = datetime.now().astimezone() - timedelta(days=30)
    assert queue.build(as_at=earlier).waiting[0].days_waiting == 0
