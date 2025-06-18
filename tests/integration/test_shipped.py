"""What ships, checked as one thing.

The other tests each hold one piece to its own standard. This asks whether the
pieces still describe the same system: that the rules the card prints are the
rules the code enforces, that the manual covers the command line that exists,
that the migrations in the package are the migrations the code loads, and that
a database built by this build passes every check this build has.

It is the test that would catch a release where everything passed and the parts
had quietly stopped agreeing with each other.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import click
import pytest

from autozest import __version__
from autozest.arbitration.policy import ArbitrationPolicy
from autozest.cli.main import cli
from autozest.defects import DefectClass
from autozest.history import current
from autozest.inspection.announcements import REGISTRY, AnnouncementCode, Light
from autozest.reporting import manual, reference
from autozest.rules import CATALOGUE, raised_in
from autozest.settlement.fees import FeeSchedule
from autozest.storage.migrator import load_migrations
from autozest.tools.demo import build as build_demo
from autozest.tools.release import checks as release_checks
from autozest.verify import verify

SOURCE = Path(__file__).resolve().parents[2] / "src" / "autozest"


def test_the_release_checks_pass() -> None:
    failed = [check for check in release_checks() if not check.passed]
    assert failed == [], "\n".join(str(check) for check in failed)


def test_a_database_this_build_makes_passes_every_check_this_build_has(
    db: sqlite3.Connection,
) -> None:
    build_demo(db, units=30, seed=41)
    result = verify(db)
    assert result.is_usable, "\n".join(str(item) for item in result.failures)


def test_the_card_prints_the_rules_the_code_enforces() -> None:
    printed = "\n".join(reference.render(ArbitrationPolicy(), FeeSchedule()))
    for defect in DefectClass:
        assert defect.value in printed, defect.value
    for code in AnnouncementCode:
        assert code.value in printed, code.value
    for light in Light:
        # The lights are printed as headings, so match either way round.
        assert light.value in printed.lower(), light.value
    for rule in CATALOGUE:
        assert rule.name in printed, rule.name


def test_the_card_does_not_invent_a_rule_the_code_does_not_have() -> None:
    assert {rule.name for rule in CATALOGUE} == raised_in(SOURCE)


def test_the_manual_covers_the_command_line_that_exists() -> None:
    printed = "\n".join(manual.render(cli))
    for name, command in cli.commands.items():
        if not isinstance(command, click.Group):
            continue
        for command_name in command.commands:
            assert f"az {name} {command_name}" in printed


def test_the_migrations_in_the_package_are_the_ones_the_code_loads() -> None:
    directory = SOURCE / "storage" / "migrations"
    on_disk = sorted(path.name for path in directory.glob("*.sql"))
    loaded = sorted(migration.filename for migration in load_migrations())
    assert on_disk == loaded
    assert len(loaded) == len({migration.number for migration in load_migrations()})


def test_every_announcement_has_a_spec_and_a_label() -> None:
    assert set(REGISTRY) == set(AnnouncementCode)
    for code, spec in REGISTRY.items():
        assert spec.label, code.value
        assert spec.code is code


def test_the_notes_describe_this_version() -> None:
    release = current()
    assert release.version == __version__
    assert release.is_released
    assert release.entries > 0


def test_the_whole_command_tree_can_be_asked_for_help() -> None:
    """Every command's help renders, which is not the same as it existing."""
    context = click.Context(cli)
    assert cli.get_help(context)
    for name, group in cli.commands.items():
        if not isinstance(group, click.Group):
            continue
        with click.Context(group, info_name=name) as group_context:
            assert group.get_help(group_context)
            for command_name, command in group.commands.items():
                with click.Context(
                    command, parent=group_context, info_name=command_name
                ) as command_context:
                    assert command.get_help(command_context)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_generated_sale_is_always_settleable(db: sqlite3.Connection, seed: int) -> None:
    """Whatever shape the generator produces, the money still adds up."""
    from autozest.block.models import RunStatus
    from autozest.block.sales import SaleRepository
    from autozest.settlement.ledger import Ledger
    from autozest.settlement.postings import PostingService

    built = build_demo(db, units=20, seed=seed)
    runs = [
        run.id
        for run in SaleRepository(db).runs(built.sale_code)
        if run.id and run.status is RunStatus.SOLD
    ]
    PostingService(db).post_sale_batch(runs)
    assert Ledger(db).is_balanced()
    assert verify(db).is_usable
