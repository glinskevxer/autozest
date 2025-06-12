from datetime import date

from autozest import __version__
from autozest.history import (
    BY_VERSION,
    HISTORY,
    Release,
    breaking_since,
    current,
    released,
    render,
)


def test_this_build_has_notes() -> None:
    assert __version__ in BY_VERSION
    assert current().version == __version__


def test_every_release_has_a_date_and_something_to_say() -> None:
    """Notes are written as the work lands, not on the day of the release."""
    for release in HISTORY:
        assert release.released_on is not None, release.version
        assert release.entries > 0, release.version


def test_breaking_changes_are_stated_in_the_imperative() -> None:
    for release in HISTORY:
        for item in release.breaking:
            assert item[:1].islower(), item
            assert not item.endswith("."), item


def test_every_version_says_something() -> None:
    for release in HISTORY:
        assert release.entries > 0, release.version


def test_no_version_appears_twice() -> None:
    versions = [release.version for release in HISTORY]
    assert len(versions) == len(set(versions))


def test_the_history_is_newest_first() -> None:
    """breaking_since walks forward from the top, so the order is load bearing."""
    dated = [release for release in HISTORY if release.released_on]
    assert dated == sorted(dated, key=lambda item: item.released_on, reverse=True)  # type: ignore[arg-type,return-value]


def test_a_version_renders_as_something_readable() -> None:
    lines = current().render()
    assert __version__ in lines[0]
    assert "2025-06-12" in lines[0]
    assert any("added" in line for line in lines)
    assert any("breaking" in line for line in lines)


def test_the_current_version_is_released() -> None:
    assert current().is_released
    assert current() in released()
    assert len(released()) == len(HISTORY)


def test_breaking_changes_are_called_out_separately() -> None:
    assert current().has_breaking_changes
    assert "python 3.12 is the minimum" in current().breaking
    assert breaking_since("0.1.0") == current().breaking


def test_breaking_since_walks_forward_from_a_version() -> None:
    history = (
        Release(
            version="1.1.0",
            released_on=date(2025, 2, 1),
            breaking=("the fee card moved",),
        ),
        Release(version="1.0.0", released_on=date(2025, 1, 1), added=("first",)),
    )
    import autozest.history as module

    original = module.HISTORY
    module.HISTORY = history
    try:
        assert module.breaking_since("1.0.0") == ("the fee card moved",)
        assert module.breaking_since("1.1.0") == ()
    finally:
        module.HISTORY = original


def test_the_whole_history_renders() -> None:
    printed = "\n".join(render())
    assert __version__ in printed
    assert "double entry ledger" in printed
