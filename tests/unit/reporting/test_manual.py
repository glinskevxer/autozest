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
