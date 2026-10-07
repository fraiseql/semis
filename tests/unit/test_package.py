"""The package imports, and its public surface is only what exists."""

import importlib
import re
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

from tests.unit.guards import named_in, text_files, tracked_text_files

ROOT = Path(__file__).parents[2]


def test_package_imports() -> None:
    package = importlib.import_module("fraiseql_semis")
    assert package.__name__ == "fraiseql_semis"


def test_subpackages_import() -> None:
    for name in ("fraiseql_semis.cli", "fraiseql_semis.providers"):
        assert importlib.import_module(name)


def test_the_version_is_the_projects() -> None:
    project = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())
    assert importlib.import_module("fraiseql_semis").__version__ == project["project"]["version"]


def test_the_documentation_url_is_where_the_site_is_served() -> None:
    root = Path(__file__).parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    config = (root / "site" / "astro.config.mjs").read_text()
    documentation = project["project"]["urls"]["Documentation"]
    assert f"site: '{documentation}'," in config


def test_all_exports_are_importable() -> None:
    package = importlib.import_module("fraiseql_semis")
    missing = [name for name in package.__all__ if not hasattr(package, name)]
    assert missing == []


# Spelled apart, so this file does not name it.
CUSTOMER = "".join(("print", "optim"))
# Development markers, spelled apart for the same reason: none ships.
MARKERS = re.compile("|".join(("pha" + "se", "to" + "do", "fix" + "me", "ha" + "ck")), re.I)


def test_the_guards_read_every_tracked_file() -> None:
    names = {path.relative_to(ROOT).as_posix() for path in tracked_text_files(ROOT)}
    assert {"uv.lock", ".python-version", ".gitignore", "pyproject.toml"} <= names
    untracked = "." + "pha" + "ses/"  # the archive's plans, never committed
    assert not any(name.startswith(untracked) for name in names)


def test_no_customer_is_named_in_the_shipped_tree() -> None:
    """semis is published; the project it was first built for is not."""
    assert named_in(ROOT, tracked_text_files(ROOT), re.compile(CUSTOMER, re.I)) == []


def test_no_development_marker_is_in_the_shipped_tree() -> None:
    """A published tree reads as written once: no plan's step, no note left for later."""
    assert named_in(ROOT, tracked_text_files(ROOT), MARKERS) == []


def test_a_planted_marker_is_found_in_a_file_or_its_name(tmp_path: Path) -> None:
    (tmp_path / "clean.py").write_text("x = 1\n")
    (tmp_path / "planted.py").write_text("# " + "TO" + "DO: later\n")
    (tmp_path / ("Pha" + "se-1.md")).write_text("")
    files = sorted(tmp_path.iterdir())
    assert named_in(tmp_path, files, MARKERS) == ["Pha" + "se-1.md", "planted.py"]


def test_a_binary_file_is_not_read(tmp_path: Path) -> None:
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00" + b"to" + b"do")
    (tmp_path / "notes.txt").write_text("plain\n")
    assert [path.name for path in text_files(sorted(tmp_path.iterdir()))] == ["notes.txt"]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[list[str], list[str], str]:
    """The sdist's and the wheel's names, and the wheel's metadata, built once."""
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        ["uv", "build", "--sdist", "--wheel", "--out-dir", str(out), str(ROOT)],
        check=True,
        capture_output=True,
    )
    (sdist,) = out.glob("*.tar.gz")
    (wheel,) = out.glob("*.whl")
    with tarfile.open(sdist) as archive:
        sdist_names = [name.partition("/")[2] for name in archive.getnames()]
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = archive.namelist()
        (metadata,) = (name for name in wheel_names if name.endswith(".dist-info/METADATA"))
        return sdist_names, wheel_names, archive.read(metadata).decode()


def test_the_site_is_in_neither_the_sdist_nor_the_wheel(
    built: tuple[list[str], list[str], str],
) -> None:
    """The site is published to semis.fraiseql.dev, not to PyPI."""
    sdist, wheel, _ = built
    assert "README.md" in sdist
    assert [name for name in sdist + wheel if name == "site" or name.startswith("site/")] == []


def test_the_wheel_says_it_is_typed(built: tuple[list[str], list[str], str]) -> None:
    _, wheel, metadata = built
    assert "fraiseql_semis/py.typed" in wheel
    assert "Classifier: Typing :: Typed" in metadata.splitlines()


def test_the_sdist_holds_no_tests() -> None:
    """The tests read the site, the workflows and git: outside the repository they fail."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    include = project["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    assert [entry for entry in include if entry.strip("/").startswith("tests")] == []
