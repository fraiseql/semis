"""The toolchain is uv, ruff, ty and pytest, and nothing it replaced."""

import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())

# Files allowed to name the retired type checker, each with its reason.
MYPY_ALLOWED = {
    "tests/unit/test_toolchain.py": "is the test",
}


def _repository_files() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [name for name in listed.stdout.splitlines() if (ROOT / name).is_file()]


def _dev_requirements() -> list[str]:
    return PYPROJECT["project"]["optional-dependencies"]["dev"]


def test_no_mypy_anywhere() -> None:
    offenders = [
        name
        for name in _repository_files()
        if name not in MYPY_ALLOWED and "mypy" in (ROOT / name).read_text(errors="ignore").lower()
    ]
    assert offenders == []


def test_mypy_allow_list_entries_still_match() -> None:
    stale = [
        name
        for name in MYPY_ALLOWED
        if "mypy" not in (ROOT / name).read_text(errors="ignore").lower()
    ]
    assert stale == []


def test_type_checker_is_ty() -> None:
    assert any(req.startswith("ty>=") for req in _dev_requirements())
    assert "ty" in PYPROJECT["tool"]


def test_confiture_floor_is_1_27() -> None:
    assert "fraiseql-confiture>=1.27,<2" in PYPROJECT["project"]["dependencies"]
