"""Scenarios: a run as a reviewable file.

A scenario names its tables, how many rows each gets, and the FK mode it runs in; semis
never infers the mode (D3). Nothing in a scenario is evaluated: a provider is named, and
the name is looked up among the providers the caller registered.
"""

import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypedDict, cast, get_args
from uuid import UUID

import yaml

from fraiseql_semis import emit, pin_file, readback, seeds
from fraiseql_semis.errors import (
    AlreadyAppliedError,
    CodeRegistryError,
    ResetBlockedError,
    ResetScopeError,
    ResolutionError,
    RowContractError,
    ScenarioError,
    SchemaNotBuiltError,
    SemisError,
    refuse_unknown,
)
from fraiseql_semis.faker_provider import LOCALES, CustomProviderRegistry, Library, Provider
from fraiseql_semis.generator import (
    Copied,
    FakeDataGenerator,
    Fill,
    Override,
    check_override_lengths,
)
from fraiseql_semis.hierarchy import Hierarchy, refuse_path_in_prep_seed
from fraiseql_semis.pin import SchemaPin, describe_changes, verify
from fraiseql_semis.resolution import EXISTING_BY
from fraiseql_semis.schema import (
    Connection,
    LiveSchema,
    ObjectRef,
    SchemaFacts,
    SeedFile,
    TableFacts,
)
from fraiseql_semis.seeds import Mode
from fraiseql_semis.staging import Staging
from fraiseql_semis.uuid_generator import scenario_bounds

MODES: tuple[Mode, ...] = get_args(Mode)

_SCENARIO_KEYS = {
    "scenario_id",
    "name",
    "description",
    "mode",
    "locale",
    "seed",
    "tables",
    "existing",
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
}
_HIERARCHY_KEYS = {"parent", "roots", "fan_out", "path"}
_EXISTING_KEYS = {"name", "where"}
_SCENARIO_ID_LIMIT = 1 << 16  # a scenario id is the UUID's fifth and sixth bytes
_FIRST_ID = 0x5001  # the first id a project's scenarios take, as the worked example does
NAME = re.compile(r"[A-Za-z0-9._-]+")
"""A scenario name: it names the scenario's file and the facts file its pin keeps."""


@dataclass(frozen=True)
class TableSpec:
    """One table of a scenario: how many rows, and what the scenario says about them.

    *overrides* maps a column to a scalar, a list with one value per row, or a callable
    taking the row's 0-based index. *providers* maps a column to the name of a registered
    provider. *fill* names the nullable columns drawn rather than written ``NULL``, or
    is ``"all"``. *trusts_trigger* names the columns a trigger fills. *hierarchy* shapes
    a table whose foreign key points at itself. *copies* maps a column to where its value
    is copied from: a column of the row one of the table's foreign keys points at.
    """

    name: str
    count: int
    overrides: Mapping[str, Override] = field(default_factory=dict)
    providers: Mapping[str, str] = field(default_factory=dict)
    fill: Fill = frozenset()
    trusts_trigger: frozenset[str] = frozenset()
    hierarchy: Hierarchy | None = None
    copies: Mapping[str, Copied] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _qualified("table", self.name)
        if type(self.count) is not int or self.count < 0:
            raise ScenarioError(
                f"{self.name}: count is {self.count!r}, not a whole number",
                resolution_hint="Give count: a whole number of rows, 0 or more.",
            )
        check_override_lengths(self.name, self.overrides, self.count)
        if self.hierarchy is not None:
            self.hierarchy.require_fan_out(self.name, self.count)
        both = sorted(set(self.overrides) & self.trusts_trigger)
        if both:
            raise ScenarioError(
                f"{self.name}: {', '.join(both)} is both overridden and trusted to a trigger",
                resolution_hint="A trigger fills a column semis leaves out; drop one of the two.",
            )
        self._check_copies()

    def _check_copies(self) -> None:
        """Refuse a copied column the table also names another way."""
        named = {
            "overridden": set(self.overrides),
            "given a provider": set(self.providers),
            "under fill:": set() if self.fill == "all" else set(self.fill),
            "trusted to a trigger": set(self.trusts_trigger),
        }
        for how, columns in named.items():
            both = sorted(columns.intersection(self.copies))
            if both:
                raise ScenarioError(
                    f"{self.name}: {', '.join(both)} is both copied and {how}",
                    resolution_hint=(
                        "A copied column holds its parent row's value; drop one of the two."
                    ),
                )


@dataclass(frozen=True)
class Deleted:
    """What a reset did to one table: the scenario's *rows* deleted, and the *kept* rows
    it still holds, which the scenario did not write. *restarted* says whether its
    identity was restarted, which it is when no row is kept; ``None`` for a table with
    no identity column."""

    table: str
    rows: int
    kept: int
    restarted: bool | None = None


@dataclass(frozen=True)
class ExistingTable:
    """A table a read-back run reads its parents from and does not write: every row,
    ordered by its surrogate key, or those whose identifier is one of *identifiers*, in
    that order."""

    name: str
    identifiers: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        _qualified("existing table", self.name)


@dataclass(frozen=True)
class Scenario:
    """A run: its id, its FK mode, its tables in the order the file lists them, the
    tables it takes parents from without writing them, and the pin of the schema it was
    written against, when it records one."""

    id: int
    name: str
    mode: Mode
    tables: tuple[TableSpec, ...]
    locale: str = "en_US"
    seed: int | None = None
    description: str = ""
    pin: SchemaPin | None = None
    existing: tuple[ExistingTable, ...] = ()

    def __post_init__(self) -> None:
        _check_name(self.name)
        if self.mode not in MODES:
            raise ScenarioError(
                f"scenario {self.name}: mode {self.mode!r} is not one of {_modes()}",
                resolution_hint=_MODE_HINT,
            )
        _check_id(self.id, f"scenario {self.name}")
        self._check_header()
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
        self._check_existing_lists(names)

    def _check_header(self) -> None:
        """Refuse a description, seed or locale of the wrong type: Faker would otherwise
        refuse the seed or the locale as a traceback, and a description is shown as text."""
        if not isinstance(self.description, str):
            raise ScenarioError(
                f"scenario {self.name}: description is {self.description!r}, not a string",
                resolution_hint="Write description: as text, quoted if it holds a colon.",
            )
        if self.seed is not None and type(self.seed) is not int:
            raise ScenarioError(
                f"scenario {self.name}: seed is {self.seed!r}, not a whole number",
                resolution_hint="Give seed: a whole number, e.g. seed: 42, or leave it out.",
            )
        if not isinstance(self.locale, str) or self.locale not in LOCALES:
            raise ScenarioError(
                f"scenario {self.name}: locale is {self.locale!r}, which is not one of "
                "Faker's locales",
                resolution_hint="Name one of Faker's locales, e.g. en_US or fr_FR.",
            )

    def _check_existing_lists(self, written: list[str]) -> None:
        """Refuse an ``existing:`` list that repeats a table, names one the run writes,
        or is given to a prep-seed scenario."""
        read = [table.name for table in self.existing]
        repeated = sorted({name for name in read if read.count(name) > 1})
        if repeated:
            raise ScenarioError(
                f"scenario {self.name} lists {', '.join(repeated)} under existing: more than once",
                resolution_hint="Give each existing table one entry.",
            )
        both = sorted(set(read) & set(written))
        if both:
            raise ScenarioError(
                f"scenario {self.name} lists {', '.join(both)} under both tables: and existing:",
                resolution_hint="A table the run reads its parents from is not one it writes.",
            )
        if self.mode == "prep-seed" and read:
            raise ScenarioError(
                f"scenario {self.name}: prep-seed cannot read {read[0]}'s existing rows: a "
                "child carries its parent's UUID, which rows semis did not write have none "
                "it can know",
                resolution_hint="Declare read-back, which reads their keys from the database.",
            )

    def for_run(
        self,
        *,
        scenario_id: int | None = None,
        seed: int | None = None,
        locale: str | None = None,
    ) -> tuple[Scenario, tuple[str, ...]]:
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


@dataclass(frozen=True)
class Run:
    """What a scenario's execution wrote: its seeds in walk order, and its schema's pin.

    *notices* says what the pin check did (matched, unpinned or skipped), for the caller
    to show.
    """

    scenario: Scenario
    seeds: tuple[SeedFile, ...]
    pin: SchemaPin
    notices: tuple[str, ...]


@dataclass(frozen=True)
class PinChange:
    """What ``ScenarioManager.pin`` found: the pin file at *path*, the schema's *pin*,
    what moved since the *previous* pin the file kept, and whether it was *written*.

    *changed* is false when the file already keeps this pin: then nothing is written.
    """

    path: Path
    pin: SchemaPin
    previous: SchemaPin | None
    changes: tuple[str, ...]
    changed: bool
    written: bool


@dataclass(frozen=True)
class Validation:
    """A prep-seed scenario's rehearsal, and confiture's report on exactly its files,
    which it wrote to *seeds_dir*, deleted since: the directory a finding's path is
    relative to."""

    run: Run
    report: seeds.PrepSeedReport
    seeds_dir: Path


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

    def load(self, path: Path | str, *, read_pin: bool = True) -> Scenario:
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
            specs = tuple(self._table(data["name"], entry) for entry in tables)
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
        caller's, and is refused before anything is written when the database already
        holds the scenario's rows; prep-seed reaches no database, so it takes none.
        """
        if connection is not None and scenario.mode == "read-back":
            self._refuse_reapply(scenario, connection)
        return self._execute(scenario, out_dir, connection=connection, format=format, no_pin=no_pin)

    def _execute(
        self,
        scenario: Scenario,
        out_dir: Path,
        *,
        connection: Connection | None,
        format: seeds.Format | None,
        no_pin: bool,
    ) -> Run:
        self._require_tables(scenario)
        self._check_providers(scenario)
        out_dir.mkdir(parents=True, exist_ok=True)
        notices = self._check(scenario, no_pin=no_pin)
        generator = self._generator(scenario)
        counts, drawn = _walk_arguments(scenario)
        if scenario.mode == "prep-seed":
            if connection is not None:
                raise ScenarioError(
                    f"scenario {scenario.name} runs in prep-seed mode, which reaches no database",
                    resolution_hint="Apply the written seeds separately, or declare read-back.",
                )
            with _naming(scenario.name):
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
            with _naming(scenario.name):
                written = emit.read_back(
                    connection,
                    generator,
                    counts,
                    out_dir,
                    **drawn,
                    format=format,
                    existing={table.name: table.identifiers for table in scenario.existing},
                )
        return Run(scenario, tuple(written), self._schema_pin(scenario), notices)

    def apply(
        self,
        scenario: Scenario,
        out_dir: Path | None = None,
        *,
        connection: Connection,
        format: seeds.Format | None = None,
        no_pin: bool = False,
    ) -> Run:
        """Run *scenario* and apply its seeds on *connection*, whose transaction stays the
        caller's.

        Read-back applies each table as it is written, as ``execute`` does. Prep-seed
        writes every file first, then applies them in the order written. A database
        that already holds the scenario's rows is refused before anything is written.
        The seed files are written to *out_dir*; without one, to a directory deleted
        before this returns, so the returned ``Run``'s paths no longer exist.
        """
        if out_dir is not None:
            return self._apply(
                scenario, out_dir, connection=connection, format=format, no_pin=no_pin
            )
        with TemporaryDirectory(prefix="semis-") as directory:
            return self._apply(
                scenario, Path(directory), connection=connection, format=format, no_pin=no_pin
            )

    def _apply(
        self,
        scenario: Scenario,
        out_dir: Path,
        *,
        connection: Connection,
        format: seeds.Format | None,
        no_pin: bool,
    ) -> Run:
        self._refuse_reapply(scenario, connection)
        if scenario.mode == "read-back":
            return self._execute(
                scenario,
                out_dir,
                connection=connection,
                format=format,
                no_pin=no_pin,
            )
        run = self._execute(scenario, out_dir, connection=None, format=format, no_pin=no_pin)
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
                return self._execute(
                    scenario,
                    Path(directory),
                    connection=None,
                    format=format,
                    no_pin=no_pin,
                )
            return self._apply(
                scenario,
                Path(directory),
                connection=connection,
                format=format,
                no_pin=no_pin,
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
            run = self._execute(
                scenario,
                Path(directory),
                connection=None,
                format=format,
                no_pin=no_pin,
            )
            report = self.validate_seeds(
                directory,
                schema_dir=schema_dir,
                max_level=max_level,
                connection=connection,
                catalog_schema=catalog_schema,
            )
        return Validation(run, report, Path(directory))

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
        checked against its table. Returns what the pin check did, then a line per table
        that leaves nullable columns ``NULL``, for the caller to show.
        """
        return self._check(scenario, no_pin=no_pin)

    def _check(self, scenario: Scenario, *, no_pin: bool) -> tuple[str, ...]:
        with _naming(scenario.name):
            return self._checked(scenario, no_pin=no_pin)

    def _checked(self, scenario: Scenario, *, no_pin: bool) -> tuple[str, ...]:
        self._require_tables(scenario)
        self._check_providers(scenario)
        tables = [spec.name for spec in scenario.tables]
        self._check_existing_keys(scenario)
        notice = verify(
            scenario.pin,
            self._facts,
            tables,
            scenario=scenario.name,
            twins=self._twins(scenario),
            existing=[table.name for table in scenario.existing],
            no_pin=no_pin,
        )
        counts, drawn = _walk_arguments(scenario)
        generator = self._generator(scenario)
        existing = frozenset(table.name for table in scenario.existing)
        generator.walk(counts, existing=existing, **drawn)
        if scenario.mode == "prep-seed":
            for table in tables:
                ref = self._facts.facts_for(table).ref
                self._staging.require(ref, self._facts.columns(self._staging.twin(ref)))
        return (notice, *_left_null_notices(generator, scenario))

    def _require_tables(self, scenario: Scenario) -> None:
        """Refuse a table *scenario* writes or reads that the schema does not hold, before
        anything asks the schema about it."""
        for name in [spec.name for spec in scenario.tables] + [
            table.name for table in scenario.existing
        ]:
            if self._facts.ref(name) is None:
                raise ScenarioError(
                    f"scenario {scenario.name}: table {name} is not in the schema",
                    resolution_hint="Name a table the schema holds, schema-qualified.",
                )

    def _check_existing_keys(self, scenario: Scenario) -> None:
        """Refuse an existing table read-back cannot point a key at: one with no
        surrogate key, or narrowed by a ``where:`` it has no identifier column for."""
        for existing in scenario.existing:
            keys = self._facts.keys_for(existing.name)
            columns = {column.name for column in self._facts.columns(existing.name) or ()}
            if keys.surrogate_pk is None:
                reason = "shows no surrogate key to point a foreign key at"
            elif existing.identifiers is not None and EXISTING_BY not in columns:
                reason = f"has no {EXISTING_BY} column for where: to name its rows by"
            else:
                continue
            raise ScenarioError(
                f"scenario {scenario.name}: existing {existing.name} {reason}",
                resolution_hint="List under existing: a trinity table, with its pk_* and identifier.",
            )

    def _refuse_reapply(self, scenario: Scenario, connection: Connection) -> None:
        """Refuse *scenario* when a table its run writes into already holds a row whose
        natural id carries the scenario's id: one query per table, on the id's range. A
        table with no natural id, or whose natural id is not a ``uuid``, is not asked.

        A prep-seed run writes into the staging twins, and their rows are promoted into
        the final tables, so both are asked. The hint's reset names every one of them.
        """
        self._require_tables(scenario)
        with _naming(scenario.name):
            written = self._written(scenario)
            found = next(
                (
                    ref
                    for ref, table in written
                    if (natural_id := self._asked_by(ref, table)) is not None
                    and readback.holds(
                        connection,
                        ref,
                        natural_id,
                        scenario_bounds(table.table_code, scenario.id),
                    )
                ),
                None,
            )
        if found is not None:
            raise AlreadyAppliedError(
                f"scenario {scenario.name} is already applied: {found.display} holds its rows",
                resolution_hint=(
                    "A scenario applies once, to a reset database. Apply it with semis "
                    "apply --reset, or run semis reset first: either deletes the "
                    "scenario's rows, and only those."
                ),
            )

    def reset(self, scenario: Scenario, *, connection: Connection) -> tuple[Deleted, ...]:
        """Delete the rows *scenario* wrote, and only those, on *connection*, whose
        transaction stays the caller's: children before parents, each table by the
        scenario's UUID range on its natural id, as the re-apply check reads it. A table
        left empty has its identity restarted, so a table the scenario owns alone
        re-applies as it first did.

        Every table is scoped before any row is deleted: one with no uuid natural id is
        refused, and nothing is deleted. A table the run does not write, an
        ``existing:`` one among them, is never touched. A caller on its own connection
        takes ``readback.exclusive`` first, as for ``apply``.
        """
        self._require_tables(scenario)
        scoped = self._scoped(scenario)
        deleted: list[Deleted] = []
        with _naming(scenario.name):
            live = LiveSchema.read(connection)
            self._refuse_pointed_at(scenario, connection, scoped, live)
            for ref, natural_id, bounds in reversed(scoped):
                rows = readback.delete_range(connection, ref, natural_id, bounds)
                kept = readback.count(connection, ref)
                identities = live.identities(ref)
                if identities and not kept:
                    readback.restart(connection, ref, identities)
                restarted = not kept if identities else None
                deleted.append(Deleted(ref.display, rows, kept, restarted))
        return tuple(deleted)

    def _refuse_pointed_at(
        self,
        scenario: Scenario,
        connection: Connection,
        scoped: list[tuple[ObjectRef, str, tuple[UUID, UUID]]],
        live: LiveSchema,
    ) -> None:
        """Refuse a reset when a row the scenario did not write points at one it did: a
        row of a table outside the run, or of a run table outside its range.

        Every foreign key into the run's tables is asked, from any schema, whatever its
        ``ON DELETE``: a ``CASCADE`` or ``SET NULL`` would otherwise reach a row semis did
        not write. Each referencing table is locked ``SHARE`` first, so no row starts
        pointing in before the delete.
        """
        ranges = {ref.display: (natural_id, bounds) for ref, natural_id, bounds in scoped}
        references = live.references_into([ref for ref, _, _ in scoped])
        referencing = {reference.table.display: reference.table for reference in references}
        readback.lock_shared(connection, [referencing[name] for name in sorted(referencing)])
        for reference in references:
            natural_id, bounds = ranges[reference.target.display]
            rows = readback.pointing(
                connection,
                reference,
                natural_id=natural_id,
                bounds=bounds,
                own=ranges.get(reference.table.display),
            )
            if rows:
                raise ResetBlockedError(
                    f"scenario {scenario.name}: "
                    + ("1 row" if rows == 1 else f"{rows} rows")
                    + f" of {reference.table.display} point{'s' if rows == 1 else ''} at its rows "
                    f"of {reference.target.display}, by {reference.constraint}",
                    resolution_hint=(
                        "semis deletes only the scenario's rows, and never through a "
                        "cascade: delete or repoint those rows first, or recreate the "
                        "database."
                    ),
                )

    def _scoped(self, scenario: Scenario) -> list[tuple[ObjectRef, str, tuple[UUID, UUID]]]:
        """Each table *scenario*'s run writes into, in insert order, with the natural id
        and the range its rows hold; refused when a table has no uuid natural id."""
        scoped: list[tuple[ObjectRef, str, tuple[UUID, UUID]]] = []
        for ref, table in self._written(scenario):
            natural_id = self._asked_by(ref, table)
            if natural_id is None:
                raise ResetScopeError(
                    f"scenario {scenario.name}: {ref.display} has no uuid id, so the "
                    "reset cannot tell the scenario's rows there from others",
                    resolution_hint=(
                        "Recreate the database, or delete that table's rows yourself; "
                        "semis deletes only rows it can find by their UUID."
                    ),
                )
            scoped.append((ref, natural_id, scenario_bounds(table.table_code, scenario.id)))
        return scoped

    def pin(self, scenario: Scenario, path: Path | str, *, check: bool = False) -> PinChange:
        """Take *scenario*'s pin from this schema, into the pin file beside its scenario
        file at *path*; with *check*, write nothing, and say only whether it would.

        The file is written only when the pin moved, so its ``taken`` date does not churn.
        A pin file that does not read is replaced: this is the call that repairs one.
        """
        target = pin_file.path_for(Path(path), scenario.name)
        current = self._schema_pin(scenario)
        try:
            previous = pin_file.read(target, scenario=scenario.name)
        except ScenarioError:
            previous = None
        if previous is not None and previous.digest == current.digest:
            return PinChange(target, current, previous, (), changed=False, written=False)
        changes = (
            ()
            if previous is None
            else describe_changes(previous.recorded or [], current.recorded or [])
        )
        if not check:
            pin_file.write(target, current)
        return PinChange(target, current, previous, changes, changed=True, written=not check)

    def _schema_pin(self, scenario: Scenario) -> SchemaPin:
        """The pin of *scenario*'s tables, twins and existing tables as this schema reads
        them."""
        self._require_tables(scenario)
        return SchemaPin.of(
            self._facts,
            [spec.name for spec in scenario.tables],
            twins=self._twins(scenario),
            existing=[table.name for table in scenario.existing],
        )

    def _written(self, scenario: Scenario) -> list[tuple[ObjectRef, TableFacts]]:
        """Each table *scenario*'s run writes into, in insert order, with the facts its
        rows are drawn from: in prep-seed, each table's staging twin after it, since the
        twins' rows are promoted into the final tables."""
        written: list[tuple[ObjectRef, TableFacts]] = []
        for ref in self._facts.insert_order([spec.name for spec in scenario.tables]):
            table = self._facts.facts_for(ref.display)
            written.append((table.ref, table))
            twin = self._facts.ref(self._staging.twin(table.ref))
            if scenario.mode == "prep-seed" and twin is not None:
                written.append((twin, table))
        return written

    def _asked_by(self, ref: ObjectRef, table: TableFacts) -> str | None:
        """The column the check asks *ref*, *table* or its twin, by for the scenario's UUID
        range: its natural id, when that is a ``uuid``. ``None`` skips it."""
        columns = self._facts.columns(ref.display) or ()
        uuids = {column.name for column in columns if column.type_key == "uuid"}
        return table.natural_id if table.natural_id in uuids else None

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

    def _table(self, scenario: str, entry: object) -> TableSpec:
        if not isinstance(entry, dict) or "name" not in entry:
            raise ScenarioError(
                f"scenario {scenario}: a table entry has no name:",
                resolution_hint=(
                    "Give each tables: entry a name:, its schema-qualified table, and a count:."
                ),
            )
        name = entry["name"]
        refuse_unknown(
            f"scenario {scenario}, table {name}", entry, _TABLE_KEYS, error=ScenarioError
        )
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
        return TableSpec(
            name=name,
            count=entry["count"],
            overrides=overrides,
            providers=cast("dict[str, str]", providers),
            fill=_fill(name, entry),
            trusts_trigger=frozenset(trusted),
            hierarchy=_hierarchy(name, entry),
            copies=_copies(name, entry),
        )

    def _check_providers(self, scenario: Scenario) -> None:
        """Refuse a ``providers:`` entry naming no registered provider, naming the scenario
        whichever caller asks: a load, a run or a check."""
        for spec in scenario.tables:
            for column, name in spec.providers.items():
                if name not in self._providers:
                    known = ", ".join(sorted(self._providers)) or "none"
                    raise ScenarioError(
                        f"scenario {scenario.name}: {spec.name}.{column} names provider "
                        f"{name!r}, which is not registered",
                        resolution_hint=f"Registered providers: {known}.",
                    )


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

_MODE_HINT = (
    "prep-seed writes UUIDs for the project's resolvers; read-back applies parents and "
    "learns their keys. semis does not choose between them."
)


_LABELS = {"id": "scenario_id", "seed": "seed", "locale": "locale"}


@contextmanager
def _naming(scenario: str) -> Iterator[None]:
    """Name *scenario* in what its file or its run refuses: a table entry, the generator
    and the resolver know only tables and columns (ARCHITECTURE §9)."""
    try:
        yield
    except (
        CodeRegistryError,
        RowContractError,
        ResolutionError,
        ScenarioError,
        SchemaNotBuiltError,
    ) as refused:
        if refused.message.startswith("scenario "):
            raise
        raise type(refused)(
            f"scenario {scenario}: {refused.message}",
            resolution_hint=refused.resolution_hint,
        ) from refused


def _left_null_notices(generator: FakeDataGenerator, scenario: Scenario) -> Iterator[str]:
    """A line per table that writes columns ``NULL`` because nobody names them: nullable
    value columns, and nullable keys whose parent has no rows in the run."""
    counts = {spec.name: spec.count for spec in scenario.tables}
    existing = frozenset(table.name for table in scenario.existing)
    parents = "tables: or existing:" if scenario.mode == "read-back" else "tables:"
    for spec in scenario.tables:
        columns = generator.left_null(
            spec.name,
            trusted=spec.trusts_trigger,
            overrides=spec.overrides,
            fill=spec.fill,
            hierarchy=spec.hierarchy,
            counts=counts,
            existing=existing,
            copies=spec.copies,
        )
        if not columns:
            continue
        keys = {
            column.name
            for column in generator.facts.facts_for(spec.name).columns
            if column.foreign_key is not None
        }
        if not keys.intersection(columns):
            hint = "fill: draws them"
        elif keys.issuperset(columns):
            hint = f"a parent under {parents} points them"
        else:
            hint = f"fill: draws the values, a parent under {parents} points the keys"
        yield f"{spec.name} leaves {', '.join(columns)} NULL; {hint}"


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


def _check_id(scenario_id: object, where: str) -> None:
    """Refuse *scenario_id* unless it fits the UUID's 16 bits; *where* names its scenario."""
    if isinstance(scenario_id, str):
        raise ScenarioError(
            f"{where}: scenario_id is {scenario_id!r}, a string",
            resolution_hint=f"Write it unquoted, in hex: scenario_id: {scenario_id}",
        )
    if type(scenario_id) is not int or not 0 <= scenario_id < _SCENARIO_ID_LIMIT:
        raise ScenarioError(
            f"{where}: scenario_id {scenario_id!r} does not fit in 16 bits",
            resolution_hint="Choose an id from 0x0 to 0xffff, written in hex.",
        )


def _check_name(name: object, where: str | None = None) -> None:
    """Refuse *name* unless it is a scenario name; *where* is the file that gives it."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ScenarioError(
            f"{f'{where}: ' if where else ''}name is {name!r}, which is not a scenario name",
            resolution_hint="Use letters, digits, ., - and _: the name becomes file names.",
        )


def _modes() -> str:
    return " or ".join(MODES)


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
    raise ScenarioError(
        f"scenario {name} {found}, where semis 0.2.0 kept its pin; the pin is now "
        f"{name}{pin_file.SUFFIX}, beside the scenario",
        resolution_hint=(
            f"Delete the schema_pin: block and {name}.facts.json, then run semis pin {path}."
        ),
    )


def _qualified(kind: str, name: object) -> None:
    """Refuse *name*, a table a scenario names, unless it carries its schema: tables are
    known by their schema-qualified name, as their codes are (D14)."""
    if not isinstance(name, str) or "." not in name:
        raise ScenarioError(
            f"{kind} {name} is not schema-qualified",
            resolution_hint="Name it with its schema, e.g. catalog.tb_city.",
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
    values = block.get(EXISTING_BY) if isinstance(block, dict) and len(block) == 1 else None
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(value, str) for value in values)
    ):
        raise ScenarioError(
            f"{where}: where: names rows by identifier, the natural key, as a list of values",
            resolution_hint="Write where: {identifier: [fr, de]}, the rows in the order given.",
        )
    return ExistingTable(name, identifiers=tuple(values))


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


_COPIES = re.compile(r"([^.]+)\.([^.]+)")
"""``<foreign key>.<parent column>``, each part a column name."""


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


class _Drawn(TypedDict):
    trusted: dict[str, frozenset[str]]
    overrides: dict[str, Mapping[str, Override]]
    fill: dict[str, Fill]
    hierarchies: dict[str, Hierarchy]
    copies: dict[str, Mapping[str, Copied]]


def _walk_arguments(scenario: Scenario) -> tuple[dict[str, int], _Drawn]:
    """The counts a scenario walks, and how each table is drawn, per table."""
    counts = {spec.name: spec.count for spec in scenario.tables}
    return counts, _Drawn(
        trusted={spec.name: spec.trusts_trigger for spec in scenario.tables},
        overrides={spec.name: spec.overrides for spec in scenario.tables},
        fill={spec.name: spec.fill for spec in scenario.tables},
        hierarchies={
            spec.name: spec.hierarchy for spec in scenario.tables if spec.hierarchy is not None
        },
        copies={spec.name: spec.copies for spec in scenario.tables if spec.copies},
    )


_ENTRIES = {"overrides": "<value>", "providers": "<provider name>"}


def _mapping(table: str, key: str, entry: Mapping[str, object]) -> dict[str, object]:
    value = entry.get(key, {})
    if not isinstance(value, dict):
        raise ScenarioError(
            f"{table}: {key} maps a column to its entry",
            resolution_hint=f"Write {key}: {{column: {_ENTRIES[key]}}}.",
        )
    return cast("dict[str, object]", value)
