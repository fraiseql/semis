"""The package imports, and its public surface is only what exists."""

import importlib
import tomllib
from pathlib import Path


def test_package_imports() -> None:
    package = importlib.import_module("fraiseql_semis")
    assert package.__name__ == "fraiseql_semis"


def test_subpackages_import() -> None:
    for name in ("fraiseql_semis.cli", "fraiseql_semis.providers"):
        assert importlib.import_module(name)


def test_the_version_is_the_projects() -> None:
    project = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())
    assert importlib.import_module("fraiseql_semis").__version__ == project["project"]["version"]


def test_all_exports_are_importable() -> None:
    package = importlib.import_module("fraiseql_semis")
    missing = [name for name in package.__all__ if not hasattr(package, name)]
    assert missing == []


# Spelled apart, so this file does not name it.
CUSTOMER = "".join(("print", "optim"))
SHIPPED_TREE = (
    "src", "tests", "docs", "scenarios", ".github", "README.md", "CHANGELOG.md", "pyproject.toml",
)  # fmt: skip


def test_no_customer_is_named_in_the_shipped_tree() -> None:
    """semis is published; the project it was first built for is not."""
    root = Path(__file__).parents[2]
    files = [
        path
        for entry in SHIPPED_TREE
        for path in ([root / entry] if (root / entry).is_file() else (root / entry).rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    ]
    named = [
        str(path.relative_to(root))
        for path in files
        if CUSTOMER in path.read_bytes().decode(errors="replace").lower()
        or CUSTOMER in str(path.relative_to(root)).lower()
    ]
    assert named == []
