"""Scenario files: a scenario's YAML read into a ``Scenario``, the scenarios of a
directory listed by their headers, and a new one written from a template.

Nothing in a file is evaluated: values are read as YAML, and a provider is a name.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal, cast

import yaml

from fraiseql_semis import pin_file
from fraiseql_semis.errors import ScenarioError, SemisError, refuse_unknown
from fraiseql_semis.generator import Copied, Fill
from fraiseql_semis.hierarchy import Hierarchy
from fraiseql_semis.resolution import EXISTING_BY
from fraiseql_semis.scenario import (
    _MODE_HINT,
    ExistingTable,
    Scenario,
    TableSpec,
    _check_id,
    _check_name,
    _modes,
    _naming,
)
from fraiseql_semis.seeds import Mode
from fraiseql_semis.translation import Locale, example_of, faker_locale, languages_of

_SCENARIO_KEYS = {
    "scenario_id",
    "name",
    "description",
    "mode",
    "locale",
    "seed",
    "tables",
    "existing",
    "locales",
    "pseudo_locale",
    "coverage",
    "tenants",
}

_TABLE_KEYS = {
    "name",
    "count",
    "overrides",
    "providers",
    "fill",
    "trusts_trigger",
    "hierarchy",
    "copies",
    "translations",
    "crosses_tenants",
}

_HIERARCHY_KEYS = {"parent", "roots", "fan_out", "path"}

_EXISTING_KEYS = {"name", "where"}

_FIRST_ID = 0x5001  # the first id a project's scenarios take, as the worked example does

_SCENARIO_YAML = (
    "Fix the YAML at that line: a scenario file is a mapping of the keys the scenario file "
    "reference lists."
)

_TABLES_HINT = "List at least one table under tables:, each with a name: and a count:."

_HIERARCHY_HINT = "Write hierarchy: {parent: <self-FK column>, roots: <n>, fan_out: <n>}."

_EXISTING_HINT = "Write existing: as a list of entries, each - name: <schema.table>."

_MISSING_HINTS = {
    "scenario_id": "Give scenario_id: an id from 0x0 to 0xffff, in hex; semis init-scenario "
    "writes the next free one.",
    "name": "Give name: the scenario's name, of letters, digits, ., - and _.",
    "tables": _TABLES_HINT,
}

_COPIES = re.compile(r"([^.]+)\.([^.]+)")
"""``<foreign key>.<parent column>``, each part a column name."""

_ENTRIES = {"overrides": "<value>", "providers": "<provider name>"}


def load(path: Path | str, *, read_pin: bool = True) -> Scenario:
    """The scenario in the YAML file at *path*, refused with ``ScenarioError`` if
    malformed, with the pin ``<name>.pin.json`` beside it keeps, when there is one.

    *read_pin* false loads it unpinned, whatever that file holds: ``semis pin``, which
    repairs the file, is not stopped by it.
    """
    data = read_yaml(Path(path), ScenarioError, _SCENARIO_YAML)
    if not isinstance(data, dict):
        raise ScenarioError(
            f"{path} does not hold a scenario mapping",
            resolution_hint="A scenario file maps scenario_id:, name:, mode: and tables:.",
        )
    where = f"{path}"
    _refuse_0_2_0_pin(Path(path), data)
    refuse_unknown(where, data, _SCENARIO_KEYS, error=ScenarioError)
    for key in ("scenario_id", "name", "tables"):
        if key not in data:
            raise ScenarioError(f"{where} has no {key}:", resolution_hint=_MISSING_HINTS[key])
    _check_name(data["name"], where)
    if "mode" not in data:
        raise ScenarioError(
            f"scenario {data['name']} declares no mode: it must be one of {_modes()}",
            resolution_hint=_MODE_HINT,
        )
    tables = data["tables"]
    if not isinstance(tables, list) or not tables:
        raise ScenarioError(
            f"scenario {data['name']} lists no tables", resolution_hint=_TABLES_HINT
        )
    with _naming(data["name"]):
        specs = tuple(_table(data["name"], entry) for entry in tables)
        existing = _existing(data["name"], data.get("existing", []))
    scenario = Scenario(
        id=data["scenario_id"],
        name=data["name"],
        mode=data["mode"],
        tables=specs,
        locale=data.get("locale", "en_US"),
        seed=data.get("seed"),
        description=data.get("description", ""),
        pin=(
            pin_file.read(pin_file.path_for(Path(path), data["name"]), scenario=data["name"])
            if read_pin
            else None
        ),
        existing=existing,
        locales=_locales(data["name"], data.get("locales"), pseudo=data.get("pseudo_locale")),
        pseudo_locale=data.get("pseudo_locale"),
        coverage=_coverage(data["name"], data.get("coverage", {})),
        tenants=_tenants(data["name"], data["tenants"]) if "tenants" in data else (),
    )
    return scenario


def _tenants(scenario: str, values: object) -> tuple[str | int, ...]:
    """The scenario's ``tenants:``: each tenant's value in the tenant column, once."""
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(value, str | int) and not isinstance(value, bool) for value in values)
    ):
        raise ScenarioError(
            f"scenario {scenario}: tenants: lists the tenants' values",
            resolution_hint="Write tenants: [<tenant id>, <tenant id>], as the tenant column holds them.",
        )
    twice = next((value for value in values if values.count(value) > 1), None)
    if twice is not None:
        raise ScenarioError(
            f"scenario {scenario}: tenants: lists {twice} twice",
            resolution_hint="List each tenant once.",
        )
    return tuple(values)


def _table(scenario: str, entry: object) -> TableSpec:
    if not isinstance(entry, dict) or "name" not in entry:
        raise ScenarioError(
            f"scenario {scenario}: a table entry has no name:",
            resolution_hint=(
                "Give each tables: entry a name:, its schema-qualified table, and a count:."
            ),
        )
    name = entry["name"]
    refuse_unknown(f"scenario {scenario}, table {name}", entry, _TABLE_KEYS, error=ScenarioError)
    if "count" not in entry:
        raise ScenarioError(
            f"scenario {scenario}: {name} has no count:",
            resolution_hint="Give count: a whole number of rows, 0 or more.",
        )
    overrides = _mapping(name, "overrides", entry)
    for column, value in overrides.items():
        if isinstance(value, dict):
            raise ScenarioError(
                f"{name}.{column}: an override is a value or a list of them, not a mapping",
                resolution_hint="Give one value for every row, or a list of one per row.",
            )
    providers = _mapping(name, "providers", entry)
    trusted = entry.get("trusts_trigger", [])
    if not all(isinstance(value, str) for value in providers.values()):
        raise ScenarioError(
            f"{name}: a provider is named, by a string",
            resolution_hint="Write providers: {column: <provider name>}.",
        )
    if not isinstance(trusted, list) or not all(isinstance(column, str) for column in trusted):
        raise ScenarioError(
            f"{name}: trusts_trigger lists column names",
            resolution_hint="Write trusts_trigger: [column, ...].",
        )
    crossing = entry.get("crosses_tenants", [])
    if not isinstance(crossing, list) or not all(isinstance(column, str) for column in crossing):
        raise ScenarioError(
            f"{name}: crosses_tenants lists column names",
            resolution_hint="Write crosses_tenants: [foreign key, ...].",
        )
    return TableSpec(
        name=name,
        count=entry["count"],
        overrides=overrides,
        providers=cast("dict[str, str]", providers),
        fill=_fill(name, entry),
        trusts_trigger=frozenset(trusted),
        hierarchy=_hierarchy(name, entry),
        copies=_copies(name, entry),
        translations=_translations(name, entry),
        crosses_tenants=frozenset(crossing),
    )


@dataclass(frozen=True)
class ScenarioEntry:
    """A scenario file as its header names it, read without its schema."""

    id: int
    name: str
    mode: str
    path: Path


def catalogue(directory: Path) -> tuple[ScenarioEntry, ...]:
    """Every scenario file under *directory*, by id and then path.

    The files are the registry of scenario ids: an id is written in its scenario and
    nowhere else. Only the header is read, so no schema is needed, and its name and id
    are checked as a load checks them. A directory that does not exist holds no
    scenarios.
    """
    entries = []
    for path in sorted(directory.rglob("*.yaml")):
        data = read_yaml(path, ScenarioError, _SCENARIO_YAML)
        if not isinstance(data, dict) or "scenario_id" not in data or "name" not in data:
            raise ScenarioError(
                f"{path} is not a scenario: it names no scenario_id and name",
                resolution_hint=(
                    "Keep only scenario files under the scenarios directory, each naming its "
                    "scenario_id: and name:."
                ),
            )
        _check_name(data["name"], str(path))
        _check_id(data["scenario_id"], str(path))
        entries.append(
            ScenarioEntry(data["scenario_id"], str(data["name"]), str(data.get("mode")), path)
        )
    return tuple(sorted(entries, key=lambda entry: (entry.id, entry.path)))


def shared_ids(entries: tuple[ScenarioEntry, ...]) -> dict[int, list[ScenarioEntry]]:
    """The scenario ids more than one file uses, each with those files."""
    by_id: dict[int, list[ScenarioEntry]] = {}
    for entry in entries:
        by_id.setdefault(entry.id, []).append(entry)
    return {id_: found for id_, found in by_id.items() if len(found) > 1}


def init_scenario(
    directory: Path, name: str, *, mode: Mode, tables: Iterable[str]
) -> tuple[Path, int]:
    """Write a new scenario named *name* into *directory*, with the next free scenario id.

    It lists *tables*, ten rows each, for the author to edit. Returns its path and id. An
    existing file is never overwritten.
    """
    _check_name(name)
    path = directory / f"{name}.yaml"
    if path.exists():
        raise ScenarioError(f"{path} already exists", resolution_hint="Choose another name.")
    entries = catalogue(directory)
    scenario_id = max((entry.id for entry in entries), default=_FIRST_ID - 1) + 1
    directory.mkdir(parents=True, exist_ok=True)
    listed = "".join(f"  - name: {table}\n    count: 10\n" for table in tables)
    path.write_text(
        f"scenario_id: {scenario_id:#06x}\n"
        f"name: {name}\n"
        'description: ""\n'
        f"mode: {mode}          # required: {_modes()}\n"
        "locale: en_US\n"
        "seed: 42\n"
        "\n"
        f"tables:\n{listed}"
    )
    return path, scenario_id


def read_yaml(path: Path, refusal: type[SemisError], hint: str) -> object:
    """The YAML document in the file at *path*, or *refusal* naming where it does not
    read, with *hint*."""
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as error:
        raise refusal(
            f"{path} does not read as YAML: {_yaml_problem(error)}", resolution_hint=hint
        ) from None


def _yaml_problem(error: yaml.YAMLError) -> str:
    """*error* on one line: what PyYAML met, and where, by line and column from 1."""
    if isinstance(error, yaml.MarkedYAMLError) and error.problem_mark is not None:
        mark = error.problem_mark
        said = ", ".join(part for part in (error.context, error.problem) if part)
        return f"{said}, at line {mark.line + 1}, column {mark.column + 1}"
    return str(error).splitlines()[0]


def _refuse_0_2_0_pin(path: Path, data: Mapping[str, object]) -> None:
    """Refuse a scenario that keeps a pin the way 0.2.0 did: a ``schema_pin:`` block, or
    the facts file beside it. Its pin lives in ``<name>.pin.json``, written by
    ``semis pin``."""
    name = data.get("name")
    facts = path.parent / f"{name}.facts.json"
    if "schema_pin" in data:
        found = "keeps a schema_pin: block"
    elif isinstance(name, str) and facts.is_file():
        found = f"has {facts.name} beside it"
    else:
        return
    who, stem = (f"scenario {name}", name) if isinstance(name, str) else (f"{path}", "<name>")
    raise ScenarioError(
        f"{who} {found}, where semis 0.2.0 kept its pin; the pin is now "
        f"{stem}{pin_file.SUFFIX}, beside the scenario",
        resolution_hint=(
            f"Delete the schema_pin: block and {stem}.facts.json, then run semis pin {path}."
        ),
    )


def _existing(scenario: str, entries: object) -> tuple[ExistingTable, ...]:
    """The scenario's ``existing:`` list."""
    if not isinstance(entries, list):
        raise ScenarioError(
            f"scenario {scenario}: existing lists tables, each by name:",
            resolution_hint=_EXISTING_HINT,
        )
    return tuple(_existing_table(scenario, entry) for entry in entries)


def _existing_table(scenario: str, entry: object) -> ExistingTable:
    if not isinstance(entry, dict) or "name" not in entry:
        raise ScenarioError(
            f"scenario {scenario}: an existing entry has no name:", resolution_hint=_EXISTING_HINT
        )
    name = entry["name"]
    where = f"scenario {scenario}, existing {name}"
    refuse_unknown(where, entry, _EXISTING_KEYS, error=ScenarioError)
    if "where" not in entry:
        return ExistingTable(name)
    block = entry["where"]
    by, values = (
        next(iter(block.items())) if isinstance(block, dict) and len(block) == 1 else (None, None)
    )
    if (
        not isinstance(by, str)
        or not isinstance(values, list)
        or not values
        or not all(isinstance(value, str) for value in values)
    ):
        raise ScenarioError(
            f"{where}: where: names rows by one column, as a list of values",
            resolution_hint=(
                f"Write where: {{{EXISTING_BY}: [fr, de]}}, or a unique text column in place of "
                f"{EXISTING_BY}: the rows in the order given."
            ),
        )
    return ExistingTable(name, by=by, values=tuple(values))


def _hierarchy(table: str, entry: Mapping[str, object]) -> Hierarchy | None:
    """The table's ``hierarchy:`` block, if it has one."""
    block = entry.get("hierarchy")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ScenarioError(
            f"{table}: hierarchy maps parent, roots and fan_out to their values",
            resolution_hint=_HIERARCHY_HINT,
        )
    refuse_unknown(f"{table}: hierarchy", block, _HIERARCHY_KEYS, error=ScenarioError)
    missing = sorted({"parent", "roots"} - set(block))
    if missing:
        raise ScenarioError(
            f"{table}: hierarchy has no {', '.join(missing)}:", resolution_hint=_HIERARCHY_HINT
        )
    try:
        return Hierarchy(block["parent"], block["roots"], block.get("fan_out"), block.get("path"))
    except ScenarioError as refused:
        raise ScenarioError(
            f"{table}: {refused.message}", resolution_hint=refused.resolution_hint
        ) from refused


def _fill(table: str, entry: Mapping[str, object]) -> Fill:
    """The table's ``fill:``: a list of column names, or ``all``."""
    fill = entry.get("fill", [])
    if fill == "all":
        return "all"
    if not isinstance(fill, list) or not all(isinstance(column, str) for column in fill):
        raise ScenarioError(
            f"{table}: fill lists column names, or is all",
            resolution_hint="Write fill: [column, ...], or fill: all.",
        )
    return frozenset(fill)


def _copies(table: str, entry: Mapping[str, object]) -> dict[str, Copied]:
    """The table's ``copies:``: each column, and the key and parent column it copies."""
    block = entry.get("copies", {})
    if not isinstance(block, dict):
        raise _malformed_copies(table)
    copies: dict[str, Copied] = {}
    for column, source in cast("dict[object, object]", block).items():
        matched = _COPIES.fullmatch(source) if isinstance(source, str) else None
        if not isinstance(column, str) or matched is None:
            raise _malformed_copies(table)
        copies[column] = Copied(*matched.groups())
    return copies


def _malformed_copies(table: str) -> ScenarioError:
    return ScenarioError(
        f"{table}: copies maps a column to <foreign key>.<parent column>",
        resolution_hint="Write copies: {column: fk_column.parent_column}.",
    )


def _mapping(table: str, key: str, entry: Mapping[str, object]) -> dict[str, object]:
    value = entry.get(key, {})
    if not isinstance(value, dict):
        raise ScenarioError(
            f"{table}: {key} maps a column to its entry",
            resolution_hint=f"Write {key}: {{column: {_ENTRIES[key]}}}.",
        )
    return cast("dict[str, object]", value)


_LOCALES_HINT = "List the locale table's codes, e.g. locales: [fr-FR, en-GB]."


def _locales(scenario: str, entries: object, *, pseudo: object = None) -> tuple[Locale, ...]:
    """The scenario's ``locales:``: each a code its Faker locale is derived from, or a
    ``{code, faker}`` mapping naming it. The *pseudo* locale, which has no Faker locale,
    is refused among them before any code is asked for its own."""
    if entries is None:
        return ()
    if not isinstance(entries, list) or not entries:
        raise _malformed_locales(scenario)
    if isinstance(pseudo, str) and pseudo in entries:
        raise ScenarioError(
            f"scenario {scenario}: {pseudo} is under both locales: and pseudo_locale:",
            resolution_hint="List the pseudo-locale under pseudo_locale: alone.",
        )
    return tuple(_locale(scenario, entry) for entry in entries)


def _locale(scenario: str, entry: object) -> Locale:
    if isinstance(entry, dict) and set(entry) == {"code", "faker"}:
        code, faker = entry["code"], entry["faker"]
        if isinstance(code, str) and isinstance(faker, str):
            return Locale(code, faker)
    if not isinstance(entry, str):
        raise _malformed_locales(scenario)
    faker = faker_locale(entry)
    if faker is None:
        named = languages_of(entry)
        has = f"Faker has {', '.join(named)}" if named else "Faker has no locale of its language"
        raise ScenarioError(
            f"scenario {scenario}: locales: {entry} has no Faker locale",
            resolution_hint=f"{has}: write {{code: {entry}, faker: {example_of(entry)}}}.",
        )
    return Locale(entry, faker)


def _malformed_locales(scenario: str) -> ScenarioError:
    return ScenarioError(
        f"scenario {scenario}: locales: is a list of locale codes, e.g. [fr-FR, en-GB]",
        resolution_hint=_LOCALES_HINT,
    )


def _translations(table: str, entry: Mapping[str, object]) -> str | Literal[True] | None:
    """The table's ``translations:``: the schema-qualified table its translations are
    written to, or ``true`` for the one the project's rule names."""
    named = entry.get("translations")
    if named is None or named is True:
        return named
    if not isinstance(named, str):
        raise ScenarioError(
            f"{table}: translations: is true or a schema-qualified table",
            resolution_hint=(
                "Write translations: true for the table semis.yaml's rule names, name the "
                "table, or leave the key out."
            ),
        )
    if "." not in named:
        raise ScenarioError(
            f"{table}'s translations: names {named}, which is not schema-qualified",
            resolution_hint="Name the translation table with its schema, e.g. catalog.tl_city.",
        )
    return named


def _coverage(scenario: str, block: object) -> dict[str, Fraction]:
    """The scenario's ``coverage:``: each locale's fraction, read exactly from the
    decimal as written, so 0.29 of 100 rows keeps 29."""
    if not isinstance(block, dict):
        raise ScenarioError(
            f"scenario {scenario}: coverage: maps a locale to a fraction, e.g. {{en-GB: 0.8}}",
            resolution_hint="Write coverage: {<locale code>: <fraction from 0 to 1>}.",
        )
    return {str(code): _fraction(scenario, str(code), value) for code, value in block.items()}


def _fraction(scenario: str, code: str, value: object) -> Fraction:
    """*value*, *code*'s coverage, as an exact fraction from 0 to 1; refused otherwise,
    ``nan``, ``inf`` and booleans included."""
    found = None
    if isinstance(value, int | float) and not isinstance(value, bool):
        try:
            found = Fraction(str(value))
        except ValueError:
            found = None  # nan and inf: no fraction
    if found is None or not 0 <= found <= 1:
        raise ScenarioError(
            f"scenario {scenario}: coverage: {code} is {value!r}, not a fraction from 0 to 1",
            resolution_hint="Give each locale's coverage from 0, no label, to 1, every label.",
        )
    return found
