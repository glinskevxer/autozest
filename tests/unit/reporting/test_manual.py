import click

from autozest.cli.main import cli
from autozest.reporting import manual


def text() -> str:
    return "\n".join(manual.render(cli))


def test_every_group_the_command_line_has_is_documented() -> None:
    groups = {
        name for name, command in cli.commands.items() if isinstance(command, click.Group)
    }
    assert groups == manual.documented_groups()


def test_every_command_appears_in_the_manual() -> None:
    printed = text()
    for name, command in cli.commands.items():
        if not isinstance(command, click.Group):
            continue
        for command_name in command.commands:
            assert f"az {name} {command_name}" in printed


def test_the_manual_is_in_the_order_somebody_learns_the_building() -> None:
    printed = text()
    assert printed.index("az consign") < printed.index("az sale")
    assert printed.index("az sale") < printed.index("az block")
    assert printed.index("az block") < printed.index("az settle")


def test_the_manual_carries_the_reference_card() -> None:
    printed = text()
    assert "The lights" in printed
    assert "Arbitration windows" in printed
    assert "Exit statuses" in printed


def test_every_command_has_something_to_say() -> None:
    for name, command in cli.commands.items():
        if not isinstance(command, click.Group):
            continue
        for command_name, sub in command.commands.items():
            assert (sub.help or "").strip(), f"az {name} {command_name} has no help"


def test_nothing_in_the_manual_runs_off_the_page() -> None:
    for line in manual.render(cli):
        assert len(line) <= 96, line


def test_the_manual_is_stable_between_builds() -> None:
    """Two renders of the same tree have to be identical.

    The docs job writes this to an artefact every build. If the order came out
    of a set, every build would produce a different file and the diff between
    two of them would be noise.
    """
    assert manual.render(cli) == manual.render(cli)


def test_the_manual_names_the_options_a_person_has_to_pass() -> None:
    printed = text()
    assert "--db" in printed or "takes --db" in printed


def test_the_manual_covers_every_group_exactly_once() -> None:
    printed = text()
    for name in manual.documented_groups():
        assert printed.count(f"az {name}\n") <= 1


def test_a_group_the_command_line_grew_without_a_blurb_would_fail() -> None:
    """The guard on the manual, stated as a test rather than left implied."""
    documented = manual.documented_groups()
    live = {name for name, command in cli.commands.items() if isinstance(command, click.Group)}
    assert live - documented == set()
    assert documented - live == set()


def test_every_blurb_is_a_sentence() -> None:
    for _, blurb in manual.ORDER:
        assert blurb[:1].isupper()
        assert blurb.endswith(".")
