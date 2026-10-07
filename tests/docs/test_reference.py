"""The site's reference pages document exactly what the code defines.

Each page's tables are read by the section they sit under, and their first column, the
names in backticks, is compared with the code: every command and option, every key of
semis.yaml and of a scenario, every provider and rule of a shipped library, every error.
A name the code gains or loses fails here until the page follows.
"""

import inspect
import re
from pathlib import Path

import pytest
from typer.main import get_group

from fraiseql_semis import TEXT_TYPES, errors, project, scenario
from fraiseql_semis.cli import app
from fraiseql_semis.providers import SHIPPED

PAGES = Path(__file__).parents[2] / "site" / "src" / "content" / "docs" / "reference"


def _section(page: str, heading: str) -> str:
    """The text under *heading*, a ``##`` or ``###`` heading, up to the next of its level."""
    text = (PAGES / page).read_text()
    found = re.search(rf"^(#{{2,3}}) {re.escape(heading)}\n(.*?)(?=^\1 |\Z)", text, re.M | re.S)
    assert found is not None, f"{page} has no section {heading}"
    return found.group(2)


def _rows(text: str) -> list[list[str]]:
    """The body rows of the first table in *text*, each a list of its cells."""
    lines = [line for line in text.splitlines() if line.startswith("|")]
    return [[cell.strip() for cell in line.strip("|").split("|")] for line in lines[2:]]


def _names(cell: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", cell))


def _first_column(page: str, heading: str) -> set[str]:
    return {name for row in _rows(_section(page, heading)) for name in _names(row[0])}


# The semis command --------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(get_group(app).commands))
def test_every_command_is_documented_with_every_option(name: str) -> None:
    command = get_group(app).commands[name]
    expected = set()
    for parameter in command.params:
        if parameter.param_type_name == "argument":
            expected.add(str(parameter.name).upper())
        else:
            expected.update(parameter.opts, parameter.secondary_opts)
    assert _first_column("cli.md", f"semis {name}") == expected


def test_the_cli_page_documents_no_command_semis_lacks() -> None:
    text = (PAGES / "cli.md").read_text()
    documented = set(re.findall(r"^## semis (\S+)$", text, re.M))
    assert documented == set(get_group(app).commands)


# semis.yaml ---------------------------------------------------------------------------


def test_every_project_key_is_documented() -> None:
    text = (PAGES / "semis-yaml.md").read_text()
    assert set(re.findall(r"^## `(\w+):`$", text, re.M)) == project._PROJECT_KEYS


def test_every_schema_source_is_documented() -> None:
    assert _first_column("semis-yaml.md", "`schema:`") == project._SCHEMA_KEYS


def test_every_prep_seed_key_is_documented() -> None:
    assert _first_column("semis-yaml.md", "`prep_seed:`") == project._PREP_SEED_KEYS


# The scenario file --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heading", "keys"),
    [
        ("Scenario keys", scenario._SCENARIO_KEYS),
        ("Table keys", scenario._TABLE_KEYS),
        ("Hierarchy keys", scenario._HIERARCHY_KEYS),
        ("Existing table keys", scenario._EXISTING_KEYS),
    ],
)
def test_every_scenario_key_is_documented(heading: str, keys: set[str]) -> None:
    assert _first_column("scenario-file.md", heading) == keys


# Shipped provider libraries -----------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_every_shipped_provider_and_its_rule_is_documented(name: str) -> None:
    library = SHIPPED[name]
    rules = {rule.provider: rule for rule in library.rules}
    documented = {}
    for provider, names, types, length, _example in _rows(_section("providers.md", f"`{name}`")):
        documented[provider.strip("`").removeprefix(f"{name}.")] = (
            _names(names),
            types,
            int(length) if length.isdigit() else 0,
        )
    expected = {
        provider: (
            set(rules[provider].names) if provider in rules else set(),
            _types(rules[provider].types) if provider in rules else "",
            rules[provider].min_length if provider in rules else 0,
        )
        for provider in library.providers
    }
    assert documented == expected


def _types(types: frozenset[str]) -> str:
    """How the page spells a rule's types: *text* for every text family."""
    return "text" if types == TEXT_TYPES else ", ".join(f"`{name}`" for name in sorted(types))


def test_the_providers_page_documents_every_shipped_library() -> None:
    text = (PAGES / "providers.md").read_text()
    assert set(re.findall(r"^## `(\w+)`$", text, re.M)) == set(SHIPPED)


# Exit codes and errors ----------------------------------------------------------------


def test_every_error_semis_raises_is_documented_with_its_code() -> None:
    expected = {
        (name, cls.error_code)
        for name, cls in inspect.getmembers(errors, inspect.isclass)
        if issubclass(cls, errors.SemisError) and cls is not errors.SemisError
    }
    documented = {
        (next(iter(_names(row[0]))), next(iter(_names(row[1]))))
        for row in _rows(_section("exit-codes.md", "semis' errors"))
    }
    assert documented == expected


def test_every_semis_error_exits_with_the_code_documented() -> None:
    codes = {
        cls.exit_code
        for cls in vars(errors).values()
        if isinstance(cls, type) and issubclass(cls, errors.SemisError)
    }
    assert codes == {1}
    rows = {row[0]: row[1] for row in _rows(_section("exit-codes.md", "Exit codes"))}
    assert "semis refused" in rows["`1`"]
