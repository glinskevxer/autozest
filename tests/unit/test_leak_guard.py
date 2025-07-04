"""The connection leak watch, driven directly.

A guard nobody tests is a guard that quietly stops working, and this one has a
failure mode that looks exactly like success: if the patched connect were never
reached, it would report zero leaks forever and read as a clean suite. So these
open real connections through it and check both answers.
"""

from __future__ import annotations

import gc
import sqlite3
from collections.abc import Iterator

import pytest

from autozest.storage.db import connect_memory
from tests.support.leaks import ConnectionWatch, Leak


@pytest.fixture()
def watch() -> Iterator[ConnectionWatch]:
    """A watch of its own, installed and taken down around the test.

    The session is already running one. Installing a second and removing it in
    the right order leaves the first in place, which the last assertion in
    test_uninstall_puts_the_original_connect_back checks by hand.
    """
    made = ConnectionWatch()
    made.install()
    try:
        yield made
    finally:
        made.uninstall()


def test_a_closed_connection_is_not_reported(watch: ConnectionWatch) -> None:
    conn = connect_memory()
    assert watch.outstanding
    conn.close()
    assert watch.outstanding == []
    assert watch.report() == ""


def test_an_unclosed_connection_is_reported_against_the_test_that_opened_it(
    watch: ConnectionWatch,
) -> None:
    watch.entering("tests/unit/test_thing.py::test_a_thing")
    leaked = connect_memory()
    assert len(watch.outstanding) == 1
    only = watch.outstanding[0]
    assert only.nodeid == "tests/unit/test_thing.py::test_a_thing"
    assert "storage/db.py" in only.origin
    report = watch.report()
    assert "1 sqlite3 connections were never closed" in report
    assert "test_a_thing" in report
    assert "3.13" in report
    leaked.close()


def test_the_report_counts_repeats_rather_than_listing_them(
    watch: ConnectionWatch,
) -> None:
    watch.entering("tests/unit/test_thing.py::test_many")
    opened = [connect_memory() for _ in range(4)]
    report = watch.report()
    assert "4 sqlite3 connections were never closed" in report
    assert report.count("test_many") == 1
    assert "x4 " in report
    for conn in opened:
        conn.close()


def test_leaks_from_different_tests_are_listed_separately(
    watch: ConnectionWatch,
) -> None:
    watch.entering("a::one")
    first = connect_memory()
    watch.entering("b::two")
    second = connect_memory()
    assert sorted(leak.nodeid for leak in watch.outstanding) == ["a::one", "b::two"]
    first.close()
    second.close()


def test_a_caller_with_its_own_factory_is_left_alone(watch: ConnectionWatch) -> None:
    """Someone else's factory is not overwritten, and untracked is the price."""
    conn = sqlite3.connect(":memory:", factory=sqlite3.Connection)
    assert watch.outstanding == []
    conn.close()


def test_installing_twice_is_refused(watch: ConnectionWatch) -> None:
    with pytest.raises(RuntimeError, match="already installed"):
        watch.install()


def test_uninstall_puts_the_original_connect_back() -> None:
    before = sqlite3.connect
    made = ConnectionWatch()
    made.install()
    assert sqlite3.connect is not before
    made.uninstall()
    assert sqlite3.connect is before
    # Uninstalling one that is not installed is a no op, not an error, so a
    # failed session teardown cannot leave the module patched.
    made.uninstall()
    assert sqlite3.connect is before


def test_a_leak_reads_as_its_test_and_its_origin() -> None:
    leak = Leak("tests/unit/test_x.py::test_y", "storage/db.py:55")
    assert str(leak) == "tests/unit/test_x.py::test_y\n    opened at storage/db.py:55"


def test_an_origin_is_relative_to_the_checkout(watch: ConnectionWatch) -> None:
    """The chain is repo relative, and no frame comes from an installed package.

    The checkout directory is itself called autozest, so a filter that matches
    on that word keeps every frame pytest is standing on and the origin reads as
    three absolute paths through site-packages.
    """
    conn = sqlite3.connect(":memory:")
    origin = watch.outstanding[0].origin
    conn.close()
    assert not origin.startswith("/")
    assert "site-packages" not in origin
    assert "tests/unit/test_leak_guard.py:" in origin


# This one leaks on purpose, so on 3.13 it trips the very ResourceWarning the
# guard exists to prevent, and warnings are errors here. Silenced for this test
# alone rather than in pyproject, where it would cover the whole suite and
# quietly undo the 3.13 leg.
@pytest.mark.filterwarnings("ignore::ResourceWarning")
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_a_leak_survives_its_connection_being_collected(watch: ConnectionWatch) -> None:
    """The entry outlives the object, because a leak is not a live connection.

    Nothing keeps a reference to the connection opened here, so it is freed as
    soon as the statement finishes. The report has to name it anyway. That is
    the whole point: on 3.13 the ResourceWarning fires at exactly this moment.
    """
    watch.entering("a::leaks")
    connect_memory()
    gc.collect()
    assert [leak.nodeid for leak in watch.outstanding] == ["a::leaks"]


def test_every_connection_is_filed_under_its_own_token(watch: ConnectionWatch) -> None:
    """Closing one connection never clears the record of a different one.

    This is the bug that made the first version of this guard useless. Leaks
    were filed under id(connection). A leaked one was freed, CPython handed the
    same address to the next connection, and closing that one cleared the entry,
    so the suite ran green with a real leak in it. Tokens are handed out in
    order and never reused, so an address coming back around means nothing.
    """
    watch.entering("a::leaks")
    leaked = connect_memory()
    seen = {leaked.token}  # type: ignore[attr-defined]
    for _ in range(50):
        later = connect_memory()
        token = later.token  # type: ignore[attr-defined]
        assert token not in seen
        seen.add(token)
        later.close()
        assert [leak.nodeid for leak in watch.outstanding] == ["a::leaks"]
    leaked.close()
    assert watch.outstanding == []
