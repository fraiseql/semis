"""Scenarios: a run as a reviewable file.

A scenario names its tables, how many rows each gets, and the FK mode it runs in; semis
never infers the mode (D3). Nothing in a scenario is evaluated: a provider is named, and
the name is looked up among the providers the caller registered.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypedDict, cast, get_args

import yaml

from fraiseql_semis import emit, seeds
from fraiseql_semis.errors import ScenarioError
from fraiseql_semis.faker_provider import CustomProviderRegistry, Library, Provider
from fraiseql_semis.generator import FakeDataGenerator, Override, check_override_lengths
from fraiseql_semis.hierarchy import Hierarchy, refuse_path_in_prep_seed
from fraiseql_semis.pin import SchemaPin, verify
from fraiseql_semis.schema import Connection, SchemaFacts, SeedFile
from fraiseql_semis.seeds import Mode
from fraiseql_semis.staging import Staging

MODES: tuple[Mode, ...] = get_args(Mode)

_SCENARIO_KEYS = {
    "scenario_id",
    "name",
    "description",
    "mode",
    "locale",
    "seed",
    "tables",
    "schema_pin",
}
PIN_FILE = "schema_pin.yaml"
SNAPSHOT_FILE = "schema_pin.ddl"  # not .sql: confiture reads every *.sql beside the seeds
_TABLE_KEYS = {"name", "count", "overrides", "providers", "trusts_trigger", "hierarchy"}
_HIERARCHY_KEYS = {"parent", "roots", "fan_out", "path"}
_SCENARIO_ID_LIMIT = 1 << 16  # a scenario id is the UUID's fifth and sixth bytes
_FIRST_ID = 0x5001  # the first id a project's scenarios take, as the worked example does
_FILE_NAME = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class TableSpec:
    """One table of a scenario: how many rows, and what the scenario says about them.

    *overrides* maps a column to a scalar, a list with one value per row, or a callable
    taking the row's 0-based index. *providers* maps a column to the name of a registered
    provider. *trusts_trigger* names the columns a trigger fills. *hierarchy* shapes a
    table whose foreign key points at itself.
    """

    name: str
    count: int
    overrides: Mapping[str, Override] = field(default_factory=dict)
    providers: Mapping[str, str] = field(default_factory=dict)
    trusts_trigger: frozenset[str] = frozenset()
    hierarchy: Hierarchy | None = None

    def __post_init__(self) -> None:
        if type(self.count) is not int or self.count < 0:
            raise ScenarioError(f"{self.name}: count is {self.count!r}, not a whole number")
        check_override_lengths(self.name, self.overrides, self.count)
        both = sorted(set(self.overrides) & self.trusts_trigger)
        if both:
            raise ScenarioError(
                f"{self.name}: {', '.join(both)} is both overridden and trusted to a trigger",
                resolution_hint="A trigger fills a column semis leaves out; drop one of the two.",
            )


@dataclass(frozen=True)
class Scenario:
    """A run: its id, its FK mode, its tables in the order the file lists them, and the
    pin of the schema it was written against, when it records one."""

    id: int
    name: str
    mode: Mode
    tables: tuple[TableSpec, ...]
    locale: str = "en_US"
    seed: int | None = None
    description: str = ""
    schema_pin: SchemaPin | None = None

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ScenarioError(
                f"scenario {self.name}: mode {self.mode!r} is not one of {_modes()}",
                resolution_hint=_MODE_HINT,
            )
        if type(self.id) is not int or not 0 <= self.id < _SCENARIO_ID_LIMIT:
            raise ScenarioError(
                f"scenario {self.name}: scenario_id {self.id!r} does not fit in 16 bits",
                resolution_hint="Choose an id from 0x0 to 0xffff, written in hex.",
            )
        if self.mode == "prep-seed":
            for spec in self.tables:
                if spec.hierarchy is not None:
                    refuse_path_in_prep_seed(spec.name, spec.hierarchy)
        names = [spec.name for spec in self.tables]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ScenarioError(
                f"scenario {self.name} lists {', '.join(repeated)} more than once",
                resolution_hint="Give each table one entry, with the total count.",
            )

    def for_run(
        self,
        *,
        scenario_id: int | None = None,
        seed: int | None = None,
        locale: str | None = None,
    ) -> tuple["Scenario", tuple[str, ...]]:
        """This scenario with one run's overrides, and a line saying each one.

        The file is not changed; an override is for the run that asked for it.
        """
        given = {"id": scenario_id, "seed": seed, "locale": locale}
        changes = {
            name: value
            for name, value in given.items()
            if value is not None and value != getattr(self, name)
        }
        notices = tuple(_override_notice(self, name, value) for name, value in changes.items())
        return replace(self, **changes), notices


def single_table(
    table: str, count: int, *, mode: Mode, scenario_id: int, seed: int | None = None
) -> Scenario:
    """A scenario of one table, named after it: what ``semis table`` runs."""
    return Scenario(
        id=scenario_id, name=table, mode=mode, tables=(TableSpec(table, count),), seed=seed
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
    nowhere else. Only the header is read, so no schema is needed. A directory that does
    not exist holds no scenarios.
    """
    entries = []
    for path in sorted(directory.rglob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict) or "scenario_id" not in data or "name" not in data:
            raise ScenarioError(f"{path} is not a scenario: it names no scenario_id and name")
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
    if not _FILE_NAME.fullmatch(name):
        raise ScenarioError(
            f"{name!r} is not a scenario name: it becomes the file name",
            resolution_hint="Use letters, digits, - and _.",
        )
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


@dataclass(frozen=True)
class Run:
    """What a scenario's execution wrote: its seeds in walk order, and its schema's pin.

    *notices* says what the pin check did (matched, unpinned or skipped), for the caller
    to show.
    """

    scenario: Scenario
    seeds: tuple[SeedFile, ...]
    pin: SchemaPin
    pin_path: Path
    notices: tuple[str, ...]


@dataclass(frozen=True)
class Validation:
    """A prep-seed scenario's rehearsal, and confiture's report on exactly its files."""

    run: Run
    report: seeds.PrepSeedReport


class ScenarioManager:
    """Loads scenarios against one schema and the providers a project registers.

    A scenario's ``providers:`` entries name one of *providers*, or a library's provider
    as ``<library>.<provider>``. Each of *libraries* also draws the columns its rules
    match, in the order given. A prep-seed scenario writes into *staging*'s twins.
    """

    def __init__(
        self,
        facts: SchemaFacts,
        *,
        providers: Mapping[str, Provider] | None = None,
        libraries: Iterable[Library] = (),
        staging: Staging | None = None,
    ) -> None:
        self._facts = facts
        self._staging = staging or Staging()
        self._libraries = tuple(libraries)
        self._providers = dict(providers or {})
        for library in self._libraries:
            for name, provider in library.named().items():
                if name in self._providers:
                    raise ValueError(f"{name} is both a provider and a library's")
                self._providers[name] = provider

    def load(self, path: Path | str) -> Scenario:
        """The scenario in the YAML file at *path*, refused with ``ScenarioError`` if malformed."""
        data = yaml.safe_load(Path(path).read_text())
        if not isinstance(data, dict):
            raise ScenarioError(f"{path} does not hold a scenario mapping")
        where = f"{path}"
        _refuse_unknown(where, data, _SCENARIO_KEYS)
        for key in ("scenario_id", "name", "tables"):
            if key not in data:
                raise ScenarioError(f"{where} has no {key}:")
        if "mode" not in data:
            raise ScenarioError(
                f"scenario {data['name']} declares no mode: it must be one of {_modes()}",
                resolution_hint=_MODE_HINT,
            )
        tables = data["tables"]
        if not isinstance(tables, list) or not tables:
            raise ScenarioError(f"scenario {data['name']} lists no tables")
        scenario = Scenario(
            id=data["scenario_id"],
            name=data["name"],
            mode=data["mode"],
            tables=tuple(self._table(data["name"], entry) for entry in tables),
            locale=data.get("locale", "en_US"),
            seed=data.get("seed"),
            description=data.get("description", ""),
            schema_pin=_pin(data, Path(path).parent),
        )
        self._check_providers(scenario)
        return scenario

    def execute(
        self,
        scenario: Scenario,
        out_dir: Path,
        *,
        connection: Connection | None = None,
        format: seeds.Format | None = None,
        no_pin: bool = False,
    ) -> Run:
        """Run *scenario* in its declared mode, writing one seed file per table to *out_dir*.

        The schema's pin is written to *out_dir* first, then checked against the one the
        scenario records: a moved schema is refused before any row is drawn, and leaves
        the new pin behind to re-pin from. *no_pin* skips the check for this run only.
        Read-back applies each table on *connection*, whose transaction stays the
        caller's; prep-seed reaches no database, so it takes none.
        """
        self._check_providers(scenario)
        out_dir.mkdir(parents=True, exist_ok=True)
        pin, pin_path = self._write_pin(scenario, out_dir)
        notices = self.check(scenario, no_pin=no_pin)
        generator = self._generator(scenario)
        counts, drawn = _walk_arguments(scenario)
        if scenario.mode == "prep-seed":
            if connection is not None:
                raise ScenarioError(
                    f"scenario {scenario.name} runs in prep-seed mode, which reaches no database",
                    resolution_hint="Apply the written seeds separately, or declare read-back.",
                )
            written = emit.prep_seed(
                generator,
                counts,
                out_dir,
                **drawn,
                format=format,
                staging=self._staging,
            )
        else:
            if connection is None:
                raise ScenarioError(
                    f"scenario {scenario.name} runs in read-back mode, which needs a connection",
                    resolution_hint="Pass the connection whose transaction the run belongs to.",
                )
            written = emit.read_back(
                connection,
                generator,
                counts,
                out_dir,
                **drawn,
                format=format,
            )
        return Run(scenario, tuple(written), pin, pin_path, notices)

    def apply(
        self,
        scenario: Scenario,
        out_dir: Path,
        *,
        connection: Connection,
        format: seeds.Format | None = None,
        no_pin: bool = False,
    ) -> Run:
        """Run *scenario* and apply its seeds on *connection*, whose transaction stays the
        caller's.

        Read-back applies each table as it is written, as ``execute`` does. Prep-seed
        writes every file first, then applies them in the order written.
        """
        if scenario.mode == "read-back":
            return self.execute(
                scenario, out_dir, connection=connection, format=format, no_pin=no_pin
            )
        run = self.execute(scenario, out_dir, format=format, no_pin=no_pin)
        seeds.apply(connection, [seed.path for seed in run.seeds])
        return run

    def rehearse(
        self,
        scenario: Scenario,
        *,
        connection: Connection | None = None,
        format: seeds.Format | None = None,
        no_pin: bool = False,
    ) -> Run:
        """*scenario* run into a directory that is deleted before this returns.

        Every row is generated and checked and every file written, then discarded: the
        returned ``Run`` says what would have been written, and its paths no longer
        exist. Given *connection*, the seeds are also applied, as ``apply`` does, and the
        caller rolls the transaction back.
        """
        with TemporaryDirectory(prefix="semis-") as directory:
            if connection is None:
                return self.execute(scenario, Path(directory), format=format, no_pin=no_pin)
            return self.apply(
                scenario, Path(directory), connection=connection, format=format, no_pin=no_pin
            )

    def validate(  # noqa: PLR0913 — one positional; the rest are keyword-only
        self,
        scenario: Scenario,
        *,
        schema_dir: Path | str,
        max_level: int = 3,
        connection: Connection | None = None,
        catalog_schema: str | None = None,
        format: seeds.Format | None = None,
        no_pin: bool = False,
    ) -> Validation:
        """*scenario* rehearsed, and its seeds judged by confiture's levels 1 to *max_level*.

        The seeds are written into a directory that holds nothing else and is deleted
        before this returns, so the report covers exactly this run. Levels 4 and 5 load
        them into the staging twins on *connection* and run the resolvers, inside a
        savepoint confiture rolls back; the transaction stays the caller's.
        """
        if scenario.mode != "prep-seed":
            raise ScenarioError(
                f"scenario {scenario.name} runs in read-back mode: "
                "confiture's five levels judge prep-seed seeds",
                resolution_hint="A read-back run is checked as it applies: semis apply --dry-run.",
            )
        with TemporaryDirectory(prefix="semis-") as directory:
            run = self.execute(scenario, Path(directory), format=format, no_pin=no_pin)
            report = self.validate_seeds(
                directory,
                schema_dir=schema_dir,
                max_level=max_level,
                connection=connection,
                catalog_schema=catalog_schema,
            )
        return Validation(run, report)

    def validate_seeds(
        self,
        seeds_dir: Path | str,
        *,
        schema_dir: Path | str,
        max_level: int = 3,
        connection: Connection | None = None,
        catalog_schema: str | None = None,
    ) -> seeds.PrepSeedReport:
        """The seeds in *seeds_dir*, judged by confiture's levels 1 to *max_level* against
        this manager's staging schema, as ``validate`` judges a rehearsal."""
        return seeds.validate(
            seeds_dir,
            schema_dir=schema_dir,
            max_level=max_level,
            database=connection,
            prep_seed_schema=self._staging.schema,
            catalog_schema=catalog_schema,
        )

    def check(self, scenario: Scenario, *, no_pin: bool = False) -> tuple[str, ...]:
        """What can be refused before a row is drawn: providers, the pin, the schema.

        The pin is verified, and the tables are ordered, found in the schema and given
        their codes, with each foreign key's parent found in the run and each hierarchy
        checked against its table. Returns what the pin check did, for the caller to show.
        """
        self._check_providers(scenario)
        tables = [spec.name for spec in scenario.tables]
        notice = verify(
            scenario.schema_pin,
            self._facts,
            tables,
            scenario=scenario.name,
            twins=self._twins(scenario),
            snapshot=_snapshot_of(scenario.schema_pin),
            no_pin=no_pin,
        )
        counts, drawn = _walk_arguments(scenario)
        self._generator(scenario).walk(counts, **drawn)
        if scenario.mode == "prep-seed":
            for table in tables:
                ref = self._facts.facts_for(table).ref
                self._staging.require(ref, self._facts.columns(self._staging.twin(ref)))
        return (notice,)

    def _twins(self, scenario: Scenario) -> list[str]:
        """The staging twins a prep-seed *scenario* writes into; none in read-back."""
        if scenario.mode != "prep-seed":
            return []
        return [
            self._staging.twin(self._facts.facts_for(spec.name).ref) for spec in scenario.tables
        ]

    def _generator(self, scenario: Scenario) -> FakeDataGenerator:
        registry = CustomProviderRegistry()
        for library in self._libraries:
            registry.register_library(library)
        for spec in scenario.tables:
            for column, name in spec.providers.items():
                registry.register_column(spec.name, column, self._providers[name])
        return FakeDataGenerator(
            self._facts,
            scenario.id,
            seed=scenario.seed,
            locale=scenario.locale,
            providers=registry,
        )

    def _write_pin(self, scenario: Scenario, out_dir: Path) -> tuple[SchemaPin, Path]:
        """The pin of *scenario*'s tables and twins as this schema reads them, written
        with its DDL snapshot."""
        tables = [spec.name for spec in scenario.tables]
        pin = SchemaPin.of(self._facts, tables, twins=self._twins(scenario))
        snapshot = self._facts.snapshot()
        if snapshot is not None:
            (out_dir / SNAPSHOT_FILE).write_text(snapshot)
            pin = pin.with_snapshot(SNAPSHOT_FILE)
        pin_path = out_dir / PIN_FILE
        pin_path.write_text(yaml.safe_dump({"schema_pin": pin.to_mapping()}, sort_keys=False))
        return pin, pin_path

    def _table(self, scenario: str, entry: object) -> TableSpec:
        if not isinstance(entry, dict) or "name" not in entry:
            raise ScenarioError(f"scenario {scenario}: a table entry has no name:")
        name = entry["name"]
        _refuse_unknown(f"scenario {scenario}, table {name}", entry, _TABLE_KEYS)
        if "count" not in entry:
            raise ScenarioError(f"scenario {scenario}: {name} has no count:")
        overrides = _mapping(name, "overrides", entry)
        for column, value in overrides.items():
            if isinstance(value, dict):
                raise ScenarioError(
                    f"{name}.{column}: an override is a value or a list of them, not a mapping"
                )
        providers = _mapping(name, "providers", entry)
        trusted = entry.get("trusts_trigger", [])
        if not all(isinstance(value, str) for value in providers.values()):
            raise ScenarioError(f"{name}: a provider is named, by a string")
        if not isinstance(trusted, list) or not all(isinstance(column, str) for column in trusted):
            raise ScenarioError(f"{name}: trusts_trigger lists column names")
        return TableSpec(
            name=name,
            count=entry["count"],
            overrides=overrides,
            providers=cast("dict[str, str]", providers),
            trusts_trigger=frozenset(trusted),
            hierarchy=_hierarchy(name, entry),
        )

    def _check_providers(self, scenario: Scenario) -> None:
        for spec in scenario.tables:
            for column, name in spec.providers.items():
                if name not in self._providers:
                    known = ", ".join(sorted(self._providers)) or "none"
                    raise ScenarioError(
                        f"{spec.name}.{column} names provider {name!r}, which is not registered",
                        resolution_hint=f"Registered providers: {known}.",
                    )


_MODE_HINT = (
    "prep-seed writes UUIDs for the project's resolvers; read-back applies parents and "
    "learns their keys. semis does not choose between them."
)


_LABELS = {"id": "scenario_id", "seed": "seed", "locale": "locale"}


def _override_notice(scenario: Scenario, name: str, value: object) -> str:
    label, was = _LABELS[name], getattr(scenario, name)
    if was is None:
        return f"scenario {scenario.name} sets no {label}; this run uses {label} {value}"
    return (
        f"scenario {scenario.name} runs with {label} {_shown(name, value)}, "
        f"not {_shown(name, was)}, for this run"
    )


def _shown(name: str, value: object) -> str:
    return f"{value:#06x}" if name == "id" else str(value)


def _modes() -> str:
    return " or ".join(MODES)


def _pin(data: Mapping[str, object], directory: Path) -> SchemaPin | None:
    """The scenario's recorded pin, its snapshot resolved beside the scenario file."""
    block = data.get("schema_pin")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ScenarioError(f"scenario {data['name']}: schema_pin is a mapping")
    pin = SchemaPin.from_mapping(block, scenario=str(data["name"]))
    if pin.snapshot is None:
        return pin
    snapshot = directory / pin.snapshot
    if not snapshot.is_file():
        raise ScenarioError(
            f"scenario {data['name']}: schema_pin names snapshot {snapshot}, which does not exist",
            resolution_hint=f"Keep the {SNAPSHOT_FILE} a run wrote beside the scenario file.",
        )
    return pin.with_snapshot(str(snapshot))


def _hierarchy(table: str, entry: Mapping[str, object]) -> Hierarchy | None:
    """The table's ``hierarchy:`` block, if it has one."""
    block = entry.get("hierarchy")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ScenarioError(f"{table}: hierarchy maps parent, roots and fan_out to their values")
    _refuse_unknown(f"{table}: hierarchy", block, _HIERARCHY_KEYS)
    missing = sorted({"parent", "roots", "fan_out"} - set(block))
    if missing:
        raise ScenarioError(f"{table}: hierarchy has no {', '.join(missing)}:")
    return Hierarchy(block["parent"], block["roots"], block["fan_out"], block.get("path"))


class _Drawn(TypedDict):
    trusted: dict[str, frozenset[str]]
    overrides: dict[str, Mapping[str, Override]]
    hierarchies: dict[str, Hierarchy]


def _walk_arguments(scenario: Scenario) -> tuple[dict[str, int], _Drawn]:
    """The counts a scenario walks, and how each table is drawn, per table."""
    counts = {spec.name: spec.count for spec in scenario.tables}
    return counts, _Drawn(
        trusted={spec.name: spec.trusts_trigger for spec in scenario.tables},
        overrides={spec.name: spec.overrides for spec in scenario.tables},
        hierarchies={
            spec.name: spec.hierarchy for spec in scenario.tables if spec.hierarchy is not None
        },
    )


def _snapshot_of(pin: SchemaPin | None) -> str | None:
    if pin is None or pin.snapshot is None:
        return None
    return Path(pin.snapshot).read_text()


def _refuse_unknown(where: str, data: Mapping[str, object], known: set[str]) -> None:
    unknown = sorted(set(data) - known)
    if unknown:
        raise ScenarioError(
            f"{where}: unknown key {', '.join(unknown)}",
            resolution_hint=f"Known keys: {', '.join(sorted(known))}.",
        )


def _mapping(table: str, key: str, entry: Mapping[str, object]) -> dict[str, object]:
    value = entry.get(key, {})
    if not isinstance(value, dict):
        raise ScenarioError(f"{table}: {key} maps a column to its entry")
    return cast("dict[str, object]", value)
