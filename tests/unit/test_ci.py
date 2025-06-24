"""The workflow and the project have to agree about what is supported.

A matrix that tests a version the package does not claim, or claims one the
matrix never runs, is worse than either on its own: the badge is green and the
thing it is green about is not the thing anybody installs.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = ROOT / "pyproject.toml"


def workflow_text() -> str:
    return WORKFLOW.read_text("utf-8")


def project() -> Any:
    """The parsed pyproject. Deliberately untyped: it is a document."""
    return tomllib.loads(PYPROJECT.read_text("utf-8"))


def test_the_workflow_is_there() -> None:
    assert WORKFLOW.exists()
    assert workflow_text().startswith("name: ci")


def test_every_job_the_build_needs_is_present() -> None:
    text = workflow_text()
    for job in ("lint:", "test:", "migrations:", "cli:", "package:"):
        assert f"\n  {job}" in text, job


def test_the_matrix_starts_at_the_supported_minimum() -> None:
    requires = str(project()["project"]["requires-python"])
    minimum = re.sub(r"[^0-9.]", "", requires)
    versions = re.findall(r'"(3\.\d+)"', workflow_text().split("matrix:")[1])
    assert versions
    assert min(versions, key=lambda item: [int(p) for p in item.split(".")]) == minimum


def test_the_tooling_the_workflow_runs_is_pinned() -> None:
    dev = project()["project"]["optional-dependencies"]["dev"]
    pinned = {entry.split("==")[0] for entry in dev if "==" in entry}
    assert {"pytest", "ruff", "mypy"} <= pinned
    assert all("==" in entry for entry in dev)


def test_the_workflow_runs_the_checks_the_repository_has() -> None:
    text = workflow_text()
    assert "ruff check ." in text
    assert "ruff format --check ." in text
    assert "python -m mypy" in text
    assert "python -m pytest" in text
    assert "autozest.tools.check_migrations" in text
    assert "autozest.tools.explain" in text


def test_the_command_line_job_exercises_a_real_sale() -> None:
    text = workflow_text()
    for command in ("admin seed", "settle post", "settle trial-balance", "report recap"):
        assert command in text


def test_the_package_job_installs_what_it_built() -> None:
    text = workflow_text()
    assert "python -m build" in text
    assert "pip install dist/" in text
    assert "az --help" in text


def test_nothing_in_the_workflow_uses_an_unpinned_action() -> None:
    for line in workflow_text().splitlines():
        if "uses:" in line:
            assert "@v" in line, line


def test_the_pinned_tooling_is_the_tooling_the_workflow_uses() -> None:
    """The workflow installs the dev extra, so the pins are what it runs.

    A workflow that pip installs ruff separately drifts from the pin inside a
    month and the first anybody knows is a rule that fires locally and not in
    the build, or the other way round.
    """
    text = workflow_text()
    assert text.count('python -m pip install -e ".[dev]"') >= 3
    assert "pip install ruff" not in text
    assert "pip install mypy" not in text
    assert "pip install pytest" not in text


def test_the_build_tool_is_pinned_too() -> None:
    assert re.search(r"pip install build==\d+\.\d+", workflow_text())


def test_the_dev_extra_carries_everything_the_workflow_runs() -> None:
    dev = project()["project"]["optional-dependencies"]["dev"]
    names = {entry.split("==")[0] for entry in dev}
    assert {"pytest", "pytest-cov", "coverage", "ruff", "mypy"} <= names


def test_the_coverage_floor_is_configured_rather_than_a_flag() -> None:
    """A floor passed on a command line is a floor somebody drops from one job."""
    coverage = project()["tool"]["coverage"]["report"]
    assert coverage["fail_under"] >= 90
    assert "--cov" in workflow_text()


def test_the_build_backend_pin_matches_the_dev_toolchain() -> None:
    build_requires = project()["build-system"]["requires"]
    assert any(entry.startswith("hatchling==") for entry in build_requires)


def test_the_docs_job_writes_the_manual_and_the_card() -> None:
    text = workflow_text()
    assert "\n  docs:" in text
    assert "admin manual --out" in text
    assert "report reference" in text
    assert "admin history" in text


def test_the_docs_are_kept_as_an_artefact() -> None:
    text = workflow_text()
    assert "upload-artifact@v4" in text
    assert "retention-days" in text


def test_every_job_that_installs_uses_the_same_two_ways() -> None:
    """Either the dev extra for checking, or a plain install for using.

    A job that installs something else is a job testing something other than
    what ships.
    """
    installs = [line.strip() for line in workflow_text().splitlines() if "pip install" in line]
    allowed = (
        'run: python -m pip install -e ".[dev]"',
        "run: python -m pip install .",
        'run: python -m pip install -e ".[dev]" pip-audit==2.7.3',
        "python -m pip install dist/*.whl",
    )
    for line in installs:
        assert line in allowed or line.startswith("run: python -m pip install build=="), line


def test_the_command_line_job_ends_by_verifying_everything() -> None:
    """The job builds a real sale and then runs every check against it.

    A smoke test that only proves the commands exit zero proves less than one
    that finishes by asking the system whether it is sound.
    """
    text = workflow_text()
    for command in ("admin doctor", "admin verify", "admin plans", "admin sizes"):
        assert command in text


def test_the_verify_step_comes_after_the_work() -> None:
    text = workflow_text()
    assert text.index("settle post") < text.index("admin verify")


def test_no_job_is_allowed_to_write_outside_the_runner_temp() -> None:
    """Anything writing into the checkout would be committed by accident."""
    for line in workflow_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("az ") and not stripped.startswith("az --help"):
            assert "--db" in line, line
            assert "$RUNNER_TEMP" in line, line


def test_the_version_is_a_real_release_number() -> None:
    version = str(project()["project"]["version"])
    parts = version.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts), version


def test_the_package_metadata_is_complete_enough_to_publish() -> None:
    """Everything a wheel needs to be identifiable once it has left here."""
    meta = project()["project"]
    for field in ("name", "version", "description", "readme", "requires-python"):
        assert meta.get(field), field
    assert meta["authors"]
    assert meta["license"]


def test_the_entry_point_is_declared() -> None:
    scripts = project()["project"]["scripts"]
    assert scripts["az"] == "autozest.cli.main:main"


def test_the_readme_the_metadata_points_at_exists() -> None:
    readme = str(project()["project"]["readme"])
    path = PYPROJECT.parent / readme
    assert path.exists()
    text = path.read_text("utf-8")
    assert "autozest" in text
    assert "az --db" in text


def test_the_readme_only_shows_commands_that_exist() -> None:
    """A readme that tells somebody to run a command that is gone is worse
    than no readme, because they will believe it."""
    import re

    import click

    from autozest.cli.main import cli

    readme = (PYPROJECT.parent / str(project()["project"]["readme"])).read_text("utf-8")
    groups = {
        name for name, command in cli.commands.items() if isinstance(command, click.Group)
    }
    for match in re.finditer(r"^az .*$", readme, re.MULTILINE):
        parts = match.group(0).split()
        without_options = [part for part in parts[1:] if not part.startswith("-")]
        if len(without_options) < 2 or without_options[0] not in groups:
            continue
        group_name, command_name = without_options[0], without_options[1]
        group = cli.commands[group_name]
        assert isinstance(group, click.Group)
        assert command_name in group.commands, match.group(0)


def test_the_audit_skips_our_own_package() -> None:
    """pip-audit --strict fails on a dependency it cannot find on PyPI.

    autozest is installed from the checkout and is not published, so without
    this the job fails on "dependency not found" rather than on anything to do
    with security, which is a red build that teaches nobody anything.
    """
    text = workflow_text()
    assert "pip_audit --strict" in text
    assert "--skip-editable" in text
