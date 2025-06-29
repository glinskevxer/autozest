from datetime import date

from autozest.tools import advisories
from autozest.tools.advisories import ACCEPTED, Accepted

SAMPLE = Accepted(
    identifier="GHSA-xxxx-yyyy-zzzz",
    package="example",
    reason="no fix released and we do not call the affected path",
    accepted_on=date(2025, 1, 10),
    review_by=date(2025, 4, 10),
)


def test_nothing_is_accepted_right_now() -> None:
    assert ACCEPTED == ()
    assert "every pin is clean" in advisories.render()[0]


def test_an_acceptance_is_current_inside_its_window() -> None:
    assert SAMPLE.is_current(date(2025, 2, 1))
    assert SAMPLE.is_current(date(2025, 1, 10))
    assert SAMPLE.is_current(date(2025, 4, 10))


def test_an_acceptance_expires() -> None:
    assert not SAMPLE.is_current(date(2025, 4, 11))
    assert SAMPLE.is_overdue(date(2025, 4, 11))
    assert not SAMPLE.is_overdue(date(2025, 4, 10))


def test_an_acceptance_does_not_apply_before_it_was_made() -> None:
    assert not SAMPLE.is_current(date(2025, 1, 9))


def test_an_acceptance_reads_as_a_decision() -> None:
    text = str(SAMPLE)
    assert "GHSA-xxxx-yyyy-zzzz" in text
    assert "review by 2025-04-10" in text
    assert "do not call the affected path" in text


def test_an_advisory_nobody_decided_about_is_reported(
    monkeypatch: object,
) -> None:
    assert advisories.unaccepted(["GHSA-aaaa-bbbb-cccc"]) == ["GHSA-aaaa-bbbb-cccc"]
    assert advisories.unaccepted([]) == []


def test_an_accepted_advisory_is_not_reported() -> None:
    import autozest.tools.advisories as module

    original = module.ACCEPTED
    module.ACCEPTED = (SAMPLE,)
    try:
        assert module.unaccepted([SAMPLE.identifier], on=date(2025, 2, 1)) == []
        assert module.unaccepted([SAMPLE.identifier], on=date(2025, 5, 1)) == [
            SAMPLE.identifier
        ]
        assert len(module.overdue(date(2025, 5, 1))) == 1
        assert "past their review date" in module.render(date(2025, 5, 1))[-1]
    finally:
        module.ACCEPTED = original


def test_the_workflow_runs_an_audit() -> None:
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text("utf-8")
    assert "pip-audit" in workflow
    assert "pip_audit --skip-editable" in workflow
