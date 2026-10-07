"""The toolchain is uv, ruff, ty and pytest, and nothing it replaced."""

import re
import subprocess
import tomllib
from pathlib import Path

import yaml

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


def test_confiture_floor_is_1_30() -> None:
    assert "fraiseql-confiture>=1.30,<2" in PYPROJECT["project"]["dependencies"]


PYTHON = "3.14"
"""The floor confiture 1.30 sets, and every tool that reads Python's version agrees."""


def test_the_package_requires_the_python_floor() -> None:
    assert PYPROJECT["project"]["requires-python"] == f">={PYTHON}"


def test_the_classifiers_name_only_the_python_floor() -> None:
    versions = [
        classifier.removeprefix("Programming Language :: Python :: ")
        for classifier in PYPROJECT["project"]["classifiers"]
        if classifier.startswith("Programming Language :: Python :: 3.")
    ]
    assert versions == [PYTHON]


def test_ruff_and_ty_target_the_python_floor() -> None:
    assert PYPROJECT["tool"]["ruff"]["target-version"] == "py" + PYTHON.replace(".", "")
    assert PYPROJECT["tool"]["ty"]["environment"]["python-version"] == PYTHON


def test_the_checkout_pins_the_python_floor() -> None:
    assert (ROOT / ".python-version").read_text().strip() == PYTHON


def test_every_workflow_sets_up_the_python_floor() -> None:
    pinned = re.compile(
        rf"astral-sh/setup-uv@\S+(?: # \S+)?\n\s+with:\n\s+python-version: '{PYTHON}'\n"
    )
    unpinned = [
        workflow.name
        for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        if (text := workflow.read_text()).count("astral-sh/setup-uv@") != len(pinned.findall(text))
    ]
    assert unpinned == []


POSTGRESQL = ("16", "18")
"""The oldest PostgreSQL semis supports, and the newest: CI runs the integration suite on both."""


def test_the_integration_suite_runs_on_the_oldest_and_newest_postgresql() -> None:
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    integration = ci["jobs"]["integration"]
    assert integration["strategy"]["matrix"]["postgresql"] == list(POSTGRESQL)
    assert integration["services"]["postgres"]["image"] == "postgres:${{ matrix.postgresql }}"


def test_the_postgresql_floor_is_stated_wherever_requirements_are() -> None:
    floor = f"PostgreSQL {POSTGRESQL[0]} or later"
    pages = ("README.md", "docs/PRD.md", "site/src/content/docs/getting-started.md")
    silent = [page for page in pages if floor not in (ROOT / page).read_text()]
    assert silent == []


def _runs(job: str) -> list[str]:
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    return [step["run"] for step in ci["jobs"][job]["steps"] if "run" in step]


def test_coverage_is_enforced_line_and_branch() -> None:
    coverage = PYPROJECT["tool"]["coverage"]
    assert (coverage["report"]["fail_under"], coverage["run"]["branch"]) == (100, True)


def test_ci_holds_the_threshold_where_the_whole_suite_runs() -> None:
    """The integration job runs every test, so coverage is whole there; the checks job
    runs the unit tests alone, so it measures nothing."""
    assert "uv run pytest" in _runs("integration")
    assert "uv run pytest -m 'not integration' --no-cov" in _runs("checks")


FLOORS = {
    "faker": "faker>=24.0.0",
    "psycopg": "psycopg[binary]>=3.2.10",
    "pyyaml": "pyyaml>=6.0.1",
    "typer": "typer>=0.19",
}
"""The lowest release of each that installs on Python 3.14 and passes the unit and contract
suites under ``uv run --resolution lowest-direct``: typer 0.12-0.18 fail on import, and
psycopg's and PyYAML's older releases have no cp314 wheel."""


def test_every_runtime_floor_is_one_semis_runs_on() -> None:
    requirements = PYPROJECT["project"]["dependencies"]
    named = {re.split(r"[\[<>=]", requirement)[0]: requirement for requirement in requirements}
    assert {name: named[name] for name in FLOORS} == FLOORS


def test_ci_tests_the_floors() -> None:
    lowest = "uv run --resolution lowest-direct --isolated --all-extras pytest -m 'not integration' --no-cov"
    assert lowest in _runs("checks")


WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$", re.M)


def test_every_action_is_pinned_to_a_commit_and_names_its_release() -> None:
    """A tag or a branch can be moved under the job that holds PyPI's identity."""
    unpinned = [
        f"{workflow.name}: {action}{rest}"
        for workflow in WORKFLOWS
        for action, rest in _USES.findall(workflow.read_text())
        if not action.startswith("./")
        and not (
            re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action)
            and re.fullmatch(r"\s+# v\d+\.\d+\.\d+", rest)
        )
    ]
    assert unpinned == []


def test_every_workflow_reads_the_repository_and_no_more_by_default() -> None:
    permissions = {
        workflow.name: yaml.safe_load(workflow.read_text()).get("permissions")
        for workflow in WORKFLOWS
    }
    assert permissions == {workflow.name: {"contents": "read"} for workflow in WORKFLOWS}


def test_publishing_waits_for_the_site_and_alone_holds_an_identity() -> None:
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "publish.yml").read_text())["jobs"]
    assert set(jobs["publish"]["needs"]) == {"build", "site"}
    assert {name: job.get("permissions") for name, job in jobs.items() if "permissions" in job} == {
        "publish": {"id-token": "write"}
    }
