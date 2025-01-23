from pathlib import Path

from autozest.rules import BY_NAME, CATALOGUE, areas, raised_in, render

SOURCE = Path(__file__).resolve().parents[2] / "src" / "autozest"


def test_the_catalogue_has_no_duplicates() -> None:
    names = [rule.name for rule in CATALOGUE]
    assert len(names) == len(set(names))


def test_every_rule_raised_in_the_source_is_in_the_catalogue() -> None:
    missing = sorted(raised_in(SOURCE) - set(BY_NAME))
    assert missing == [], f"undocumented rules: {missing}"


def test_every_rule_in_the_catalogue_is_actually_raised() -> None:
    raised = raised_in(SOURCE)
    stale = sorted(name for name in BY_NAME if name not in raised)
    assert stale == [], f"rules nothing raises: {stale}"


def test_every_rule_is_area_dot_name() -> None:
    for rule in CATALOGUE:
        assert "." in rule.name
        assert rule.name.islower()
        assert rule.detail
        assert rule.area


def test_the_areas_group_cleanly() -> None:
    grouped = areas()
    assert sum(len(rules) for rules in grouped.values()) == len(CATALOGUE)
    assert "arbitration" in grouped
    assert "settlement" in grouped


def test_the_rendered_catalogue_reads() -> None:
    printed = "\n".join(render())
    assert "arb.announced" in printed
    assert f"{len(CATALOGUE)} rules" in printed
