"""Everything that has to be true before a version goes out.

Run as ``python -m autozest.tools.release``. It is the last thing anybody does
before tagging, and it exists because the three things that have gone wrong at
that moment were all cheap to check and none of them were checked.

A version in two places that disagree. A migration added and never applied to
anything, so the first upgrade in the field was the first time it ran. And a
data file that lives in the source tree, works in every test, and is not in the
wheel, so the installed package cannot find its own migrations.

Nothing here is clever. It is a list of the things that have actually broken.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from autozest import __version__
from autozest.storage.db import connect_memory
from autozest.storage.migrator import default_directory as default_migrations_directory
from autozest.storage.migrator import load_migrations, migrate
from autozest.tools.check_migrations import problems as migration_problems
from autozest.tools.explain import plans


@dataclass(frozen=True)
class Check:
    """One thing that has to be true."""

    name: str
    passed: bool
    detail: str

    def __str__(self) -> str:
        return f"{'ok  ' if self.passed else 'FAIL'} {self.name}: {self.detail}"


def project_root() -> Path | None:
    """The source checkout, when there is one.

    An installed package has no checkout above it, and walking three parents
    from site-packages lands somewhere arbitrary. Returning None says so
    instead of pointing at a directory that happens to exist.
    """
    candidate = Path(__file__).resolve().parents[3]
    return candidate if (candidate / "pyproject.toml").exists() else None


def declared_version(root: Path | None = None) -> str | None:
    """The version in pyproject, or None when running from an installed wheel."""
    base = root or project_root()
    if base is None:
        return None
    pyproject = base / "pyproject.toml"
    if not pyproject.exists():
        return None
    return str(tomllib.loads(pyproject.read_text("utf-8"))["project"]["version"])


def checks(root: Path | None = None) -> list[Check]:
    """Everything, in the order it is worth reading."""
    found: list[Check] = []

    declared = declared_version(root)
    found.append(
        Check(
            "version",
            declared is None or declared == __version__,
            f"package says {__version__}, pyproject says {declared}"
            if declared
            else f"package says {__version__}, no checkout to compare against",
        )
    )

    migrations = load_migrations()
    numbers = [migration.number for migration in migrations]
    found.append(
        Check(
            "migrations numbered",
            numbers == list(range(1, len(numbers) + 1)),
            f"{len(numbers)} migrations, contiguous from one",
        )
    )

    conn = connect_memory()
    migrate(conn)
    bad = migration_problems()
    found.append(Check("migrations sound", not bad, bad[0] if bad else "no schema problems"))

    scanning = [plan for plan in plans(conn) if not plan.uses_an_index]
    found.append(
        Check(
            "query plans",
            not scanning,
            f"{len(scanning)} of the watched queries scan a table",
        )
    )
    conn.close()

    found.append(_packaging_check())
    return found


def _packaging_check() -> Check:
    """Every migration the code loads has to be a file inside the package.

    This is the check that caught itself. It used to look under a source tree,
    which does not exist once the wheel is installed, so it reported zero sql
    files on a package that had all of them. It asks the package where its own
    migrations directory is now, which is the same question the loader asks
    and the only one that means anything when installed.
    """
    directory = default_migrations_directory()
    on_disk = sorted(path.name for path in directory.glob("*.sql"))
    expected = sorted(migration.filename for migration in load_migrations())
    first = expected[0] if expected else "none"
    return Check(
        "migrations packaged",
        bool(on_disk) and on_disk == expected,
        f"{len(on_disk)} sql files beside the package at {directory.parent.name}, "
        f"first is {first}",
    )


def main() -> int:
    found = checks()
    for check in found:
        print(str(check))
    failed = [check for check in found if not check.passed]
    print(f"{len(found)} checks, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
