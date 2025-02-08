"""The workflow and the project have to agree about what is supported.

A matrix that tests a version the package does not claim, or claims one the
matrix never runs, is worse than either on its own: the badge is green and the
thing it is green about is not the thing anybody installs.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = ROOT / "pyproject.toml"


def workflow_text() -> str:
    return WORKFLOW.read_text("utf-8")


def project() -> dict[str, object]:
    return tomllib.loads(PYPROJECT.read_text("utf-8"))


def test_the_workflow_is_there() -> None:
    assert WORKFLOW.exists()
    assert workflow_text().startswith("name: ci")


def test_every_job_the_build_needs_is_present() -> None:
    text = workflow_text()
    for job in ("lint:", "test:", "migrations:", "cli:", "package:"):
        assert f"\n  {job}" in text, job


def test_the_matrix_starts_at_the_supported_minimum() -> None:
    requires = str(project()["project"]["requires-python"])  # type: ignore[index]
    minimum = re.sub(r"[^0-9.]", "", requires)
    versions = re.findall(r'"(3\.\d+)"', workflow_text().split("matrix:")[1])
    assert versions
    assert min(versions, key=lambda item: [int(p) for p in item.split(".")]) == minimum


def test_the_tooling_the_workflow_runs_is_pinned() -> None:
    dev = project()["project"]["optional-dependencies"]["dev"]  # type: ignore[index]
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
