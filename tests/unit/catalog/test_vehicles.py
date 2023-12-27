import sqlite3
from datetime import date

import pytest

from autozest.catalog.odometer import OdometerStatus, Reading
from autozest.catalog.vehicles import OdometerHistory, Vehicle, VehicleRepository
from autozest.errors import Conflict, NotFound, ValidationError
from autozest.vin import Vin

ACCORD = "1HGCM82633A004352"
MODEL3 = "5YJ3E1EA0KF000316"
SALE = date(2024, 3, 12)


def accord(**kwargs: object) -> Vehicle:
    defaults: dict[str, object] = {
        "vin": Vin.parse(ACCORD),
        "make": "Honda",
        "model": "Accord",
        "series": "EX-L",
        "body_style": "sedan",
        "drivetrain": "fwd",
        "fuel": "gasoline",
    }
    defaults.update(kwargs)
    return Vehicle(**defaults)  # type: ignore[arg-type]


def reading(miles: int, day: int = 1) -> Reading:
    return Reading(miles=miles, read_on=date(2024, 1, day), source="intake")


@pytest.fixture()
def repo(db: sqlite3.Connection) -> VehicleRepository:
    return VehicleRepository(db)


def test_the_model_year_comes_off_the_vin_when_nobody_supplies_one() -> None:
    assert accord().year == 2003


def test_a_supplied_model_year_wins_over_the_vin() -> None:
    assert accord(model_year=2004).year == 2004


def test_the_description_is_what_goes_on_a_lot_card() -> None:
    assert accord().description == "2003 Honda Accord EX-L"
    assert accord(series=None).description == "2003 Honda Accord"


def test_round_trip(repo: VehicleRepository) -> None:
    repo.add(accord())
    found = repo.get(ACCORD)
    assert found.make == "Honda"
    assert found.drivetrain == "fwd"
    assert found.check_digit_ok
    assert found.vin.value == ACCORD


def test_a_vehicle_can_be_fetched_by_its_parsed_vin(repo: VehicleRepository) -> None:
    repo.add(accord())
    assert repo.get(Vin.parse(ACCORD)).model == "Accord"


def test_an_unknown_vin_raises_not_found(repo: VehicleRepository) -> None:
    with pytest.raises(NotFound):
        repo.get(MODEL3)
    assert repo.find(MODEL3) is None


def test_a_second_add_of_the_same_vin_is_a_conflict(repo: VehicleRepository) -> None:
    repo.add(accord())
    with pytest.raises(Conflict):
        repo.add(accord())
    with pytest.raises(Conflict, match="already on file"):
        repo.require_absent(ACCORD)


def test_upsert_returns_what_is_already_there_without_changing_it(
    repo: VehicleRepository,
) -> None:
    repo.add(accord())
    again = repo.upsert(accord(model="Accord Coupe"))
    assert again.model == "Accord"


def test_an_import_without_a_valid_check_digit_is_stored_and_flagged(
    repo: VehicleRepository,
) -> None:
    broken = Vin.parse(ACCORD[:8] + "9" + ACCORD[9:], require_check_digit=False)
    repo.add(accord(vin=broken))
    assert repo.get(broken.value).check_digit_ok is False


def test_readings_need_a_vehicle_on_file_first(repo: VehicleRepository) -> None:
    with pytest.raises(ValidationError, match="no vehicle on file"):
        repo.record_reading(ACCORD, reading(41_000))


def test_readings_come_back_in_the_order_they_were_recorded(
    repo: VehicleRepository,
) -> None:
    repo.add(accord())
    for miles in (41_000, 41_800, 42_150):
        repo.record_reading(ACCORD, reading(miles))
    history = repo.history(ACCORD)
    assert [r.miles for r in history.readings] == [41_000, 41_800, 42_150]
    assert history.latest is not None
    assert history.latest.miles == 42_150
    assert history.highest is not None
    assert history.highest.miles == 42_150


def test_an_empty_history_has_no_latest_and_no_highest(repo: VehicleRepository) -> None:
    repo.add(accord())
    history = repo.history(ACCORD)
    assert history.readings == []
    assert history.latest is None
    assert history.highest is None


def test_the_highest_reading_is_not_always_the_last_one(repo: VehicleRepository) -> None:
    repo.add(accord())
    for miles in (180_000, 90_000):
        repo.record_reading(ACCORD, reading(miles))
    history = repo.history(ACCORD)
    assert history.latest is not None and history.latest.miles == 90_000
    assert history.highest is not None and history.highest.miles == 180_000


def test_a_history_across_two_visits_shows_the_rollback(repo: VehicleRepository) -> None:
    repo.add(accord(model_year=2019, vin=Vin.parse(MODEL3)))
    repo.record_reading(MODEL3, reading(88_400, day=3))
    repo.record_reading(MODEL3, reading(31_100, day=9))
    history = repo.history(MODEL3)
    assert history.status(2019, SALE) is OdometerStatus.NOT_ACTUAL


def test_a_clean_history_reads_actual(repo: VehicleRepository) -> None:
    repo.add(accord(model_year=2019, vin=Vin.parse(MODEL3)))
    repo.record_reading(MODEL3, reading(31_100, day=3))
    repo.record_reading(MODEL3, reading(33_450, day=9))
    assert repo.history(MODEL3).status(2019, SALE) is OdometerStatus.ACTUAL


def test_history_status_passes_the_replaced_cluster_through() -> None:
    history = OdometerHistory(vin=MODEL3, readings=[reading(180_000), reading(12)])
    assert history.status(2019, SALE, cluster_replaced=True) is OdometerStatus.NOT_ACTUAL


def test_readings_go_when_the_vehicle_goes(db: sqlite3.Connection) -> None:
    repo = VehicleRepository(db)
    repo.add(accord())
    repo.record_reading(ACCORD, reading(41_000))
    db.execute("DELETE FROM vehicle WHERE vin = ?", (ACCORD,))
    assert repo.history(ACCORD).readings == []


def test_search_narrows_on_either_field_and_sorts_newest_first(
    repo: VehicleRepository,
) -> None:
    repo.add(accord())
    repo.add(accord(vin=Vin.parse(MODEL3), make="Tesla", model="Model 3", series=None))
    assert [v.make for v in repo.search()] == ["Tesla", "Honda"]
    assert [v.make for v in repo.search(make="Honda")] == ["Honda"]
    assert [v.make for v in repo.search(model_year=2019)] == ["Tesla"]
    assert repo.search(make="Honda", model_year=2019) == []
