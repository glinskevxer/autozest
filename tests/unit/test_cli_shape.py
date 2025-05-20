"""The command line has to be usable by somebody who has not read the source.

Everything here is checked against the whole command tree rather than a list,
so a command added next year is held to it without anybody remembering to add
it to a test.
"""

from __future__ import annotations

import click

from autozest.cli.main import cli


def groups() -> list[tuple[str, click.Group]]:
    return [
        (name, command)
        for name, command in sorted(cli.commands.items())
        if isinstance(command, click.Group)
    ]


def commands() -> list[tuple[str, str, click.Command]]:
    found: list[tuple[str, str, click.Command]] = []
    for name, group in groups():
        for command_name, command in sorted(group.commands.items()):
            found.append((name, command_name, command))
    return found


def test_there_is_a_command_tree_to_check() -> None:
    assert len(groups()) >= 12
    assert len(commands()) >= 60


def test_every_group_says_what_it_is_for() -> None:
    for name, group in groups():
        assert (group.help or "").strip(), name


def test_every_command_says_what_it_does() -> None:
    for group, name, command in commands():
        assert (command.help or "").strip(), f"az {group} {name}"


def test_every_help_starts_with_a_capital_and_ends_with_a_stop() -> None:
    """The opposite of the error style, and deliberately so.

    An error message is the tail of a sentence somebody else started. A help
    line is the whole sentence.
    """
    for group, name, command in commands():
        first = (command.help or "").strip().splitlines()[0]
        assert first[:1].isupper(), f"az {group} {name}: {first}"
        assert first.endswith("."), f"az {group} {name}: {first}"


def test_no_help_line_runs_off_a_terminal() -> None:
    for group, name, command in commands():
        first = (command.help or "").strip().splitlines()[0]
        assert len(first) <= 78, f"az {group} {name}: {len(first)} characters"


def test_every_option_says_what_it_is_for() -> None:
    """A flag with no help is a flag nobody uses and nobody removes."""
    exempt = {"--help", "--version"}
    for group, name, command in commands():
        for parameter in command.params:
            if not isinstance(parameter, click.Option):
                continue
            if set(parameter.opts) & exempt:
                continue
            if parameter.is_flag or parameter.default is None:
                assert parameter.help, f"az {group} {name} {parameter.opts}"


def test_nothing_takes_more_than_four_positional_arguments() -> None:
    """Past four nobody remembers the order and everything wants a flag."""
    for group, name, command in commands():
        positional = [p for p in command.params if isinstance(p, click.Argument)]
        assert len(positional) <= 4, f"az {group} {name} takes {len(positional)}"


def test_the_top_level_options_are_on_the_group_and_not_repeated() -> None:
    top = {opt for parameter in cli.params for opt in parameter.opts}
    assert {"--db", "--config", "--log"} <= top
    for group, name, command in commands():
        opts = {opt for parameter in command.params for opt in parameter.opts}
        assert "--db" not in opts, f"az {group} {name} takes its own --db"


def test_no_two_groups_share_a_name() -> None:
    names = [name for name, _ in groups()]
    assert len(names) == len(set(names))
