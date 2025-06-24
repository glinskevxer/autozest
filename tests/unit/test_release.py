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
    assert "sql files beside the package" in by_name["migrations packaged"].detail


def test_main_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert release.main() == 0
    output = capsys.readouterr().out
    assert "0 failed" in output
    assert "version" in output


def test_main_exits_one_when_something_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        release, "checks", lambda _root=None: [release.Check("x", False, "broken")]
    )
    assert release.main() == 1
    assert "FAIL x" in capsys.readouterr().out


def test_the_version_is_the_one_the_notes_describe() -> None:
    from autozest.history import current

    assert current().version == __version__
    assert current().is_released


def test_this_is_a_one_point_release() -> None:
    """The shape of the number is part of the promise it makes.

    A one point zero says the command line and the exit statuses are an
    interface now, and the breaking list in the notes is what changed to get
    there.
    """
    major, minor, patch = (int(part) for part in __version__.split("."))
    assert (major, minor, patch) == (1, 0, 0)


def test_the_checks_pass_when_there_is_no_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This is the check that caught itself.

    Installed from a wheel there is no source tree above the package, and the
    packaging check used to look for one and report zero sql files on a package
    that had all of them. It asks the package where its own migrations live
    now, which is the same question the loader asks.
    """
    monkeypatch.setattr(release, "project_root", lambda: None)
    results = release.checks()
    failed = [check for check in results if not check.passed]
    assert failed == [], "\n".join(str(check) for check in failed)

    packaged = next(check for check in results if check.name == "migrations packaged")
    assert "29 sql files" in packaged.detail or "sql files beside" in packaged.detail

    version = next(check for check in results if check.name == "version")
    assert "no checkout to compare against" in version.detail


def test_project_root_is_none_when_there_is_no_pyproject_above_us(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(release, "__file__", str(tmp_path / "a" / "b" / "c" / "release.py"))
    assert release.project_root() is None


def test_the_packaging_check_reads_the_package_not_a_source_tree() -> None:
    """It has to find the migrations through the package itself.

    A path built from the checkout works in every test and is wrong the moment
    the wheel is installed, which is exactly how this shipped broken.
    """
    from autozest.storage.migrator import default_directory

    packaged = next(check for check in release.checks() if check.name == "migrations packaged")
    assert packaged.passed
    assert default_directory().name == "migrations"
    assert (default_directory() / "001_core.sql").exists()
