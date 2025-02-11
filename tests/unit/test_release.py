from pathlib import Path

import pytest

from autozest import __version__
from autozest.tools import release


def test_every_release_check_passes() -> None:
    failed = [check for check in release.checks() if not check.passed]
    assert failed == [], "\n".join(str(check) for check in failed)


def test_the_two_versions_agree() -> None:
    assert release.declared_version() == __version__


def test_a_missing_pyproject_does_not_fail_the_version_check(tmp_path: Path) -> None:
    assert release.declared_version(tmp_path) is None
    version_check = next(check for check in release.checks(tmp_path) if check.name == "version")
    assert version_check.passed


def test_the_checks_are_named_and_explained() -> None:
    for check in release.checks():
        assert check.name
        assert check.detail
        assert str(check).startswith("ok")


def test_the_migrations_are_contiguous_and_packaged() -> None:
    by_name = {check.name: check for check in release.checks()}
    assert by_name["migrations numbered"].passed
    assert by_name["migrations packaged"].passed
    assert "sql files under the package" in by_name["migrations packaged"].detail


def test_main_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert release.main() == 0
    output = capsys.readouterr().out
    assert "0 failed" in output
    assert "version" in output


def test_main_exits_one_when_something_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        release, "checks", lambda root=None: [release.Check("x", False, "broken")]
    )
    assert release.main() == 1
    assert "FAIL x" in capsys.readouterr().out
