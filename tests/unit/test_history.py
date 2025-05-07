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
    assert "unreleased" in lines[0]
    assert any("added" in line for line in lines)


def test_an_unreleased_version_says_so() -> None:
    assert not current().is_released
    assert released() == ()


def test_breaking_changes_are_called_out_separately() -> None:
    assert not current().has_breaking_changes
    assert breaking_since("0.0.1") == ()


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
