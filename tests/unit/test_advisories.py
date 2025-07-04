import io
import json
from datetime import date
from pathlib import Path

import pytest

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


AUDIT = json.dumps(
    {
        "dependencies": [
            {"name": "click", "version": "8.1.8", "vulns": []},
            {
                "name": "pytest",
                "version": "8.3.5",
                "vulns": [
                    {"id": "PYSEC-2025-1", "fix_versions": ["9.0.3"], "aliases": []},
                    {"id": "GHSA-abcd", "fix_versions": [], "aliases": []},
                ],
            },
        ],
        "fixes": [],
    }
)


def test_an_audit_report_reads_as_findings() -> None:
    found = advisories.from_audit(AUDIT)
    assert [f.identifier for f in found] == ["GHSA-abcd", "PYSEC-2025-1"]
    assert found[1].package == "pytest"
    assert found[1].version == "8.3.5"
    assert found[1].fixed_in == ("9.0.3",)
    assert str(found[1]) == "PYSEC-2025-1 pytest 8.3.5 (fixed in 9.0.3)"
    assert found[0].remedy == "no fix published"


@pytest.mark.parametrize(
    "payload", ["", "not json at all", "[]", '"a string"', "{}", '{"dependencies": null}']
)
def test_a_report_that_is_not_the_shape_we_expect_is_no_findings(payload: str) -> None:
    """pip-audit grows keys between releases and a KeyError here reads as a
    broken build rather than as a tool that moved on."""
    assert advisories.from_audit(payload) == []


def test_a_dependency_entry_that_is_junk_is_stepped_over() -> None:
    payload = json.dumps(
        {
            "dependencies": [
                "not a table",
                {"name": "x", "version": "1", "vulns": ["also not a table", {}]},
                {"name": "y", "version": "2", "vulns": [{"id": "PYSEC-2025-9"}]},
            ]
        }
    )
    assert [f.identifier for f in advisories.from_audit(payload)] == ["PYSEC-2025-9"]


def test_a_summary_marks_each_finding_decided_or_not() -> None:
    original = advisories.ACCEPTED
    try:
        advisories.ACCEPTED = (
            advisories.Accepted(
                identifier="PYSEC-2025-1",
                package="pytest",
                reason="only reachable from the assertion rewriter",
                accepted_on=date(2025, 3, 1),
                review_by=date(2025, 9, 1),
            ),
        )
        lines = advisories.summary(advisories.from_audit(AUDIT), on=date(2025, 4, 1))
        assert any("accepted: PYSEC-2025-1" in line for line in lines)
        assert any("undecided: GHSA-abcd" in line for line in lines)
        assert lines[-1] == "2 reported, 1 nobody has decided about, 0 past review"
    finally:
        advisories.ACCEPTED = original


def test_a_summary_calls_out_an_acceptance_that_has_run_out() -> None:
    original = advisories.ACCEPTED
    try:
        advisories.ACCEPTED = (
            advisories.Accepted(
                identifier="PYSEC-2025-1",
                package="pytest",
                reason="waiting on a release",
                accepted_on=date(2025, 3, 1),
                review_by=date(2025, 4, 1),
            ),
        )
        lines = advisories.summary(advisories.from_audit(AUDIT), on=date(2025, 9, 1))
        assert any(line.startswith("  OVERDUE:") for line in lines)
        assert any("undecided: PYSEC-2025-1" in line for line in lines)
        assert lines[-1] == "2 reported, 2 nobody has decided about, 1 past review"
    finally:
        advisories.ACCEPTED = original


def test_an_empty_audit_says_so() -> None:
    assert advisories.summary([]) == [
        "the audit found nothing",
        "0 reported, 0 nobody has decided about, 0 past review",
    ]


def test_the_command_reports_and_a_reported_advisory_does_not_fail_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A new advisory against a pin is not something the build gets to act on."""
    report = tmp_path / "audit.json"
    report.write_text(AUDIT, "utf-8")
    assert advisories.main(["--from-audit", str(report)]) == 0
    out = capsys.readouterr().out
    assert "undecided: PYSEC-2025-1" in out
    assert "2 reported" in out


def test_the_command_reads_standard_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(AUDIT))
    assert advisories.main(["--from-audit", "-"]) == 0
    assert "undecided: GHSA-abcd" in capsys.readouterr().out


def test_the_command_runs_with_no_report_at_all(capsys: pytest.CaptureFixture[str]) -> None:
    assert advisories.main([]) == 0
    assert "no advisories accepted" in capsys.readouterr().out


def test_an_overdue_acceptance_is_what_fails_the_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    original = advisories.ACCEPTED
    try:
        advisories.ACCEPTED = (
            advisories.Accepted(
                identifier="PYSEC-2020-1",
                package="old",
                reason="was going to look again",
                accepted_on=date(2020, 1, 1),
                review_by=date(2020, 2, 1),
            ),
        )
        assert advisories.main([]) == 1
        assert "past their review date" in capsys.readouterr().err
    finally:
        advisories.ACCEPTED = original


def test_a_missing_report_is_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """pip-audit writing nothing is a normal outcome of a step allowed to fail."""
    missing = tmp_path / "never-written.json"
    assert advisories.main(["--from-audit", str(missing)]) == 0
    assert "nothing to check" in capsys.readouterr().out
