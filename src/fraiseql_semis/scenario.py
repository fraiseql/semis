"""Scenarios: a run as a reviewable file, and the manager that runs one.

A scenario names its tables, how many rows each gets, and the FK mode it runs in; semis
never infers the mode (D3). Nothing in a scenario is evaluated: a provider is named, and
the name is looked up among the providers the caller registered. ``scenario_file``
reads a scenario's YAML into this module's model.
"""

import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, TypedDict, cast, get_args
from uuid import UUID

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
from fraiseql_semis.pin import PinnedRead, SchemaPin, describe_changes, merge_reads, verify
from fraiseql_semis.resolution import EXISTING_BY
from fraiseql_semis.rows import declared_length
from fraiseql_semis.schema import (
    ColumnReference,
    Connection,
    LiveSchema,
    ObjectRef,
    SchemaFacts,
    SeedFile,
    TableFacts,
)
from fraiseql_semis.seeds import Mode
from fraiseql_semis.staging import Staging
from fraiseql_semis.translation import (
    MAX_LOCALES,
    Locale,
    Translation,
    TranslationRule,
    TranslationTable,
    example_of,
    locale_keys,
    locale_reads,
)
from fraiseql_semis.uuid_generator import scenario_bounds

MODES: tuple[Mode, ...] = get_args(Mode)

_BRACKETED = 3  # the shortest pseudo label a column holds: its brackets and one letter
_SCENARIO_ID_LIMIT = 1 << 16  # a scenario id is the UUID's fifth and sixth bytes
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
    *translations* names the table each row's translations are written to, one row per
    locale the scenario declares, or is ``True`` for the table the project's rule names.
    """

    name: str
    count: int
    overrides: Mapping[str, Override] = field(default_factory=dict)
    providers: Mapping[str, str] = field(default_factory=dict)
    fill: Fill = frozenset()
    trusts_trigger: frozenset[str] = frozenset()
    hierarchy: Hierarchy | None = None
    copies: Mapping[str, Copied] = field(default_factory=dict)
    translations: str | Literal[True] | None = None

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
    locales: tuple[Locale, ...] = ()
    pseudo_locale: str | None = None
    coverage: Mapping[str, Fraction] = field(default_factory=dict)

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
        self._check_translations(names)

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
        """Refuse an ``existing:`` list that repeats a table, or names one the run writes."""
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

    def _check_translations(self, written: list[str]) -> None:
        """Refuse locales and translated tables the scenario contradicts: one without
        the other, a locale twice or too many, or a translation table also listed."""
        translated = [spec for spec in self.tables if spec.translations is not None]
        self._check_pseudo_locale()
        self._check_coverage()
        if translated and not self.locales:
            raise ScenarioError(
                f"scenario {self.name}: {translated[0].name} has translations:, and the "
                "scenario declares no locales:",
                resolution_hint="Declare the locale codes it is translated into: locales: [fr-FR, en-GB].",
            )
        if self.locales and not translated:
            raise ScenarioError(
                f"scenario {self.name} declares locales:, and no table has translations:",
                resolution_hint="Name, under a table's translations:, the table its translations are written to.",
            )
        codes = [locale.code for locale in self.locales]
        twice = next((code for code in codes if codes.count(code) > 1), None)
        if twice is not None:
            raise ScenarioError(
                f"scenario {self.name}: locales: lists {twice} twice",
                resolution_hint="List each locale code once.",
            )
        if len(codes) > MAX_LOCALES:
            raise ScenarioError(
                f"scenario {self.name}: locales: holds {len(codes)} locales; a scenario "
                f"writes at most {MAX_LOCALES}",
                resolution_hint=f"Translate into at most {MAX_LOCALES} locales in one scenario.",
            )
        for locale in self.locales:
            if locale.faker not in LOCALES:
                raise ScenarioError(
                    f"scenario {self.name}: locales: {locale.code} maps to faker "
                    f"{locale.faker}, which is not one of Faker's locales",
                    resolution_hint=f"Name one of Faker's locales, e.g. {example_of(locale.code)}.",
                )
        read = {table.name for table in self.existing}
        for spec in translated:
            listed = (
                "tables:"
                if spec.translations in written
                else "existing:"
                if spec.translations in read
                else None
            )
            if listed is not None:
                raise ScenarioError(
                    f"scenario {self.name}: {spec.translations} is written by {spec.name}'s "
                    f"translations: and listed under {listed}",
                    resolution_hint="List it once: its rows are written for each translated row.",
                )

    def _check_coverage(self) -> None:
        """Refuse coverage of a locale the scenario does not declare, or with none."""
        if not self.coverage:
            return
        if not self.locales:
            raise ScenarioError(
                f"scenario {self.name}: coverage: applies to locales:, and the scenario "
                "declares none",
                resolution_hint="Declare the locales first; coverage leaves some labels out.",
            )
        declared = {locale.code for locale in self.locales} | {self.pseudo_locale}
        undeclared = next((code for code in self.coverage if code not in declared), None)
        if undeclared is not None:
            raise ScenarioError(
                f"scenario {self.name}: coverage: names {undeclared}, which the scenario does "
                "not declare",
                resolution_hint="Name, under coverage:, a locale of locales: or pseudo_locale:.",
            )

    def _check_pseudo_locale(self) -> None:
        """Refuse a pseudo-locale that is not one code, or that has no locale to
        transform the labels of, or is one of them."""
        pseudo = self.pseudo_locale
        if pseudo is None:
            return
        if not isinstance(pseudo, str) or not pseudo:
            raise ScenarioError(
                f"scenario {self.name}: pseudo_locale: is one locale code, e.g. qps-ploc",
                resolution_hint="Name the locale table's code for the pseudo-locale.",
            )
        if not self.locales:
            raise ScenarioError(
                f"scenario {self.name}: pseudo_locale: transforms the first locale's labels, "
                "and the scenario declares no locales:",
                resolution_hint=(
                    "Declare the locales first; the pseudo-locale transforms the first one's labels."
                ),
            )
        if pseudo in {locale.code for locale in self.locales}:
            raise ScenarioError(
                f"scenario {self.name}: {pseudo} is under both locales: and pseudo_locale:",
                resolution_hint="List the pseudo-locale under pseudo_locale: alone.",
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

    *changes* names what moved in the facts; *stale*, a pinned row gone or whose UUID
    changed; *added*, rows a whole-table read gained; *missing*, a read the previous pin
    lacked. *changed* is false when the file already keeps this pin: then nothing is
    written.
    """

    path: Path
    pin: SchemaPin
    previous: SchemaPin | None
    changes: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    changed: bool = True
    written: bool = False

    @property
    def wrong(self) -> bool:
        """Whether the seeds the previous pin gives are wrong: there was none, the facts
        moved, a pinned row is stale, or a read is missing. Rows added are not wrong."""
        return self.previous is None or bool(self.changes or self.stale or self.missing)


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
        translations: TranslationRule | None = None,
    ) -> None:
        self._facts = facts
        self._staging = staging or Staging()
        self._rule = translations or TranslationRule()
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
        repairs the file, is not stopped by it. A provider no one registered is refused.
        """
        # scenario_file builds this module's model, so it imports this module.
        from fraiseql_semis import scenario_file  # noqa: PLC0415 — the one cycle, broken here

        scenario = scenario_file.load(path, read_pin=read_pin)
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
                    existing={
                        table.name: [
                            UUID(uuid) for _, uuid in self._pinned_existing(scenario, table).rows
                        ]
                        for table in scenario.existing
                    },
                    translations=self._translations(scenario),
                    locales={
                        table: {
                            str(code): UUID(uuid)
                            for code, uuid in self._pinned_read(
                                scenario, table, column, tuple(codes)
                            ).rows
                        }
                        for table, (column, codes) in locale_reads(
                            self._translations(scenario)
                        ).items()
                    },
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
                    translations=self._translations(scenario),
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
        self._translation_tables(scenario)
        tables = self._written_tables(scenario)
        self._check_existing_keys(scenario)
        if (
            scenario.mode == "read-back"
            and scenario.pin is not None
            and scenario.pin.rows is not None
        ):
            raise ScenarioError(
                f"scenario {scenario.name} runs in read-back mode, and its pin records rows, "
                "which only a prep-seed run reads: its mode changed since it was pinned",
                resolution_hint="Re-take the pin with semis pin on the scenario.",
            )
        notice = verify(
            scenario.pin,
            self._facts,
            tables,
            scenario=scenario.name,
            twins=self._twins(scenario),
            existing=[table.name for table in scenario.existing],
            coded=self._coded(scenario),
            no_pin=no_pin,
        )
        if scenario.mode == "prep-seed":
            for existing in scenario.existing:
                self._pinnable(scenario, existing.name)
                self._pinned_existing(scenario, existing)
            for table, (column, codes) in locale_reads(self._translations(scenario)).items():
                self._pinned_read(scenario, table, column, tuple(codes))
        counts, drawn = _walk_arguments(scenario)
        generator = self._generator(scenario)
        existing = frozenset(table.name for table in scenario.existing)
        generator.walk(
            counts, existing=existing, translations=self._translations(scenario), **drawn
        )
        if scenario.mode == "prep-seed":
            for table in tables:
                ref = self._facts.facts_for(table).ref
                self._staging.require(ref, self._facts.columns(self._staging.twin(ref)))
        return (notice, *_table_notices(generator, scenario, self._translations(scenario)))

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

        Every table is scoped before any row is deleted: a translation table with no uuid
        natural id through its entity key, any other one with none refused, and nothing
        deleted. A table the run does not write, an
        ``existing:`` one among them, is never touched. A caller on its own connection
        takes ``readback.exclusive`` first, as for ``apply``.
        """
        self._require_tables(scenario)
        deleted: list[Deleted] = []
        with _naming(scenario.name):
            scoped = self._scoped(scenario)
            live = LiveSchema.read(connection)
            self._refuse_pointed_at(scenario, connection, scoped, live)
            for ref, scope in reversed(scoped):
                rows = readback.delete(connection, ref, scope)
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
        scoped: list[tuple[ObjectRef, readback.Scope]],
        live: LiveSchema,
    ) -> None:
        """Refuse a reset when a row the scenario did not write points at one it did: a
        row of a table outside the run, or of a run table outside its range.

        Every foreign key into the run's tables is asked, from any schema, whatever its
        ``ON DELETE``: a ``CASCADE`` or ``SET NULL`` would otherwise reach a row semis did
        not write. Each referencing table is locked ``SHARE`` first, so no row starts
        pointing in before the delete.
        """
        scopes = {ref.display: scope for ref, scope in scoped}
        references = live.references_into([ref for ref, _ in scoped])
        referencing = {reference.table.display: reference.table for reference in references}
        readback.lock_shared(connection, [referencing[name] for name in sorted(referencing)])
        for reference in references:
            rows = readback.pointing(
                connection,
                reference,
                scope=scopes[reference.target.display],
                own=scopes.get(reference.table.display),
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

    def _scoped(self, scenario: Scenario) -> list[tuple[ObjectRef, readback.Scope]]:
        """Each table *scenario*'s run writes into, in insert order, with where its rows
        lie: its natural id's range, or, for a translation table with no uuid id, its
        entity's, through the entity key. Refused when a table has neither."""
        translated = {table.table: table for table in self._translation_tables(scenario)}
        scoped: list[tuple[ObjectRef, readback.Scope]] = []
        for ref, table in self._written(scenario):
            natural_id = self._asked_by(ref, table)
            written = translated.get(table.ref.display)
            if natural_id is not None:
                bounds = scenario_bounds(table.table_code, scenario.id)
                scoped.append((ref, readback.Scope(natural_id, bounds)))
            elif written is not None:
                scoped.append((ref, self._through(scenario, ref, table, written)))
            else:
                raise ResetScopeError(
                    f"scenario {scenario.name}: {ref.display} has no uuid id, so the "
                    "reset cannot tell the scenario's rows there from others",
                    resolution_hint=(
                        "Recreate the database, or delete that table's rows yourself; "
                        "semis deletes only rows it can find by their UUID."
                    ),
                )
        return scoped

    def _through(
        self, scenario: Scenario, ref: ObjectRef, table: TableFacts, written: TranslationTable
    ) -> readback.Scope:
        """Where *written*'s rows of *scenario* lie, its table or its twin at *ref*: those
        whose entity key points at one of the scenario's entity rows. In the twin, the
        key holds the entity's UUID, so its range is the entity's own."""
        entity = self._facts.facts_for(written.entity)
        bounds = scenario_bounds(entity.table_code, scenario.id)
        if ref != table.ref:
            return readback.Scope(f"{written.entity_key}_id", bounds)
        key = next(column for column in table.columns if column.name == written.entity_key)
        reference = cast(ColumnReference, key.foreign_key)
        return readback.Scope(
            cast("str", entity.natural_id),
            bounds,
            readback.Through(
                written.entity_key, entity.ref, reference.column or cast("str", entity.surrogate_pk)
            ),
        )

    def pin(
        self,
        scenario: Scenario,
        path: Path | str,
        *,
        check: bool = False,
        connection: Connection | None = None,
    ) -> PinChange:
        """Take *scenario*'s pin from this schema, into the pin file beside its scenario
        file at *path*; with *check*, write nothing, and say only whether it would.

        A prep-seed scenario's pin also records the existing rows it points at, read on
        *connection*, which the caller rolls back. The file is written only when the pin
        moved, so its ``taken`` date does not churn; a whole-table read keeps the rows it
        recorded in their order, and appends the rows it gained. A pin file that does not
        read is replaced: this is the call that repairs one.
        """
        target = pin_file.path_for(Path(path), scenario.name)
        with _naming(scenario.name):
            current = self._schema_pin(scenario)
        try:
            previous = pin_file.read(target, scenario=scenario.name)
        except ScenarioError:
            previous = None
        merged = None
        if self.reads_rows(scenario):
            with _naming(scenario.name):
                reads = self._read_rows(scenario, connection)
            merged = merge_reads(None if previous is None else previous.rows or (), reads)
            current = replace(current, rows=merged.reads)
        if (
            previous is not None
            and previous.digest == current.digest
            and previous.rows == current.rows
        ):
            return PinChange(target, current, previous, changed=False, written=False)
        changes = (
            ()
            if previous is None
            else describe_changes(previous.recorded or [], current.recorded or [])
        )
        if not check:
            pin_file.write(target, current)
        return PinChange(
            target,
            current,
            previous,
            changes,
            stale=() if merged is None else merged.stale,
            added=() if merged is None else merged.added,
            missing=() if merged is None else merged.missing,
            changed=True,
            written=not check,
        )

    def reads_rows(self, scenario: Scenario) -> bool:
        """Whether *scenario*'s pin records rows: a prep-seed scenario pointing at rows it
        does not write, under ``existing:`` or in a locale table."""
        return scenario.mode == "prep-seed" and (
            bool(scenario.existing) or any(spec.translations for spec in scenario.tables)
        )

    def _read_rows(
        self, scenario: Scenario, connection: Connection | None
    ) -> tuple[PinnedRead, ...]:
        """The existing rows *scenario* points at, one read per ``existing:`` table, each
        row's ``identifier`` (when the table has one) and UUID, sorted by table."""
        if connection is None:
            first = (
                scenario.existing[0].name
                if scenario.existing
                else next(iter(locale_reads(self._translations(scenario))))
            )
            raise ScenarioError(
                f"scenario {scenario.name}: pinning {first}'s rows needs a database; pass -d",
                resolution_hint=(
                    "Pass --database-url, or set CONFITURE_DATABASE_URL: the UUIDs of the rows "
                    "the scenario points at are read from it."
                ),
            )
        reads = []
        for existing in scenario.existing:
            natural_id = self._pinnable(scenario, existing.name)
            by = self._read_by(existing)
            rows = readback.existing_rows(
                connection,
                self._facts.keys_for(existing.name),
                by=by,
                values=existing.identifiers,
                natural_id=natural_id,
            )
            reads.append(
                PinnedRead(
                    existing.name,
                    by,
                    existing.identifiers,
                    tuple((value, str(uuid)) for _, value, uuid in rows),
                )
            )
        for table, (column, codes) in locale_reads(self._translations(scenario)).items():
            natural_id = self._pinnable(scenario, table, locale=True)
            try:
                found = readback.existing_rows(
                    connection,
                    self._facts.keys_for(table),
                    by=column,
                    values=None,
                    natural_id=natural_id,
                )
            except ResolutionError:
                found = []  # a locale table holding no row: the first code is refused below
            keys = locale_keys(
                ((code, str(uuid)) for _, code, uuid in found), codes, table=table, column=column
            )
            reads.append(
                PinnedRead(
                    table, column, tuple(codes), tuple((code, str(keys[code])) for code in codes)
                )
            )
        return tuple(sorted(reads, key=lambda read: (read.table, read.by or "")))

    def _translation_tables(self, scenario: Scenario) -> list[TranslationTable]:
        """Each translated table's translation table, found in the schema, its locale
        table one this mode can point a key at: by its ``pk_*`` in read-back, by a uuid
        id in prep-seed."""
        found = []
        listed = {spec.name: "tables:" for spec in scenario.tables} | {
            table.name: "existing:" for table in scenario.existing
        }
        for spec in scenario.tables:
            if spec.translations is None:
                continue
            name = self._translation_table(spec)
            if name in listed:
                raise ScenarioError(
                    f"{name} is written by {spec.name}'s translations: and listed under "
                    f"{listed[name]}",
                    resolution_hint="List it once: its rows are written for each translated row.",
                )
            table = TranslationTable.of(self._facts, spec.name, name, self._rule)
            if scenario.pseudo_locale is not None:
                self._fits_pseudo_labels(table)
            if scenario.mode == "prep-seed":
                self._pinnable(scenario, table.locale_table, locale=True)
            elif self._facts.keys_for(table.locale_table).surrogate_pk is None:
                raise ScenarioError(
                    f"{table.locale_table} shows no surrogate key to point a locale key at",
                    resolution_hint="Read-back points a foreign key at a pk_* column.",
                )
            found.append(table)
        return found

    def _fits_pseudo_labels(self, table: TranslationTable) -> None:
        """Refuse a label column too short to hold a bracketed pseudo label."""
        label = next(
            column
            for column in self._facts.columns(table.table) or ()
            if column.name == table.label
        )
        length = declared_length(label)
        if length is not None and length < _BRACKETED:
            raise ScenarioError(
                f"{table.table}.{table.label} is {label.type_key}, too short for a bracketed "
                "pseudo label",
                resolution_hint="Widen the label column, or drop pseudo_locale:.",
            )

    def _translation_table(self, spec: TableSpec) -> str:
        """The translation table *spec* names, or, for ``translations: true``, the one the
        project's rule names; refused when the rule names one the schema lacks."""
        if spec.translations is not True:
            return cast("str", spec.translations)
        name = self._rule.table_for(spec.name)
        if self._facts.ref(name) is None:
            raise ScenarioError(
                f"{spec.name}'s translations, by semis.yaml's rule '{self._rule.table}', are "
                f"{name}, which the schema does not hold",
                resolution_hint="Name the table under translations:, or change the rule in semis.yaml.",
            )
        return name

    def _translations(self, scenario: Scenario) -> dict[str, Translation]:
        """What a run of *scenario* writes for each translated table: its translation
        table, in each of the scenario's locales."""
        return {
            table.entity: Translation(
                table, scenario.locales, scenario.pseudo_locale, scenario.coverage
            )
            for table in self._translation_tables(scenario)
        }

    def _coded(self, scenario: Scenario) -> dict[str, str]:
        """Each locale table *scenario*'s translations point at, with its code column."""
        return {table.locale_table: table.code for table in self._translation_tables(scenario)}

    def _read_by(self, existing: ExistingTable) -> str | None:
        """The column *existing*'s rows are named by in a pin: ``identifier`` for a
        ``where:`` read, or when the table has one; ``None`` otherwise."""
        names = {column.name for column in self._facts.columns(existing.name) or ()}
        if existing.identifiers is not None or EXISTING_BY in names:
            return EXISTING_BY
        return None

    def _pinned_read(
        self,
        scenario: Scenario,
        table: str,
        by: str | None,
        where: tuple[str, ...] | None,
    ) -> PinnedRead:
        """The read *scenario*'s pin records of *table*, its rows named by *by*, those
        *where* names: the rows a prep-seed child points at by UUID. Refused when the pin
        records none, or not the rows named."""
        reads = scenario.pin.rows if scenario.pin is not None else None
        found = next((read for read in reads or () if read.key == (table, by, where)), None)
        if found is not None:
            return found
        pinned = {
            value
            for read in reads or ()
            if read.table == table and read.by == by
            for value, _ in read.rows
        }
        unpinned = next((value for value in where or () if pinned and value not in pinned), None)
        said = (
            "its pin records none"
            if unpinned is None
            else f"its pin records no row whose {by} is {unpinned!r}"
        )
        raise ScenarioError(
            f"scenario {scenario.name}: prep-seed points at {table}'s rows by UUID, and {said}",
            resolution_hint=(
                "Record them with semis pin on the scenario, -d naming the database they are "
                "read from."
            ),
        )

    def _pinned_existing(self, scenario: Scenario, existing: ExistingTable) -> PinnedRead:
        """The read *scenario*'s pin records for the ``existing:`` table *existing*."""
        return self._pinned_read(
            scenario, existing.name, self._read_by(existing), existing.identifiers
        )

    def _pinnable(self, scenario: Scenario, table: str, *, locale: bool = False) -> str:
        """*table*'s natural id, the UUID a prep-seed child carries for one of its rows;
        refused when it has none that is a uuid. A *locale* table is named as one."""
        keys = self._facts.keys_for(table)
        columns = {column.name: column for column in self._facts.columns(table) or ()}
        natural = columns.get(keys.natural_id or "")
        if natural is None or natural.type_key != "uuid":
            what, hint = (
                (f"locale table {table}", "Give the locale table a UUID id")
                if locale
                else (
                    f"existing {table}",
                    "List under existing: a table whose rows carry a UUID id",
                )
            )
            raise ScenarioError(
                f"scenario {scenario.name}: {what} has no uuid id for a prep-seed child to carry",
                resolution_hint=f"{hint}, or run the scenario in read-back.",
            )
        return natural.name

    def _schema_pin(self, scenario: Scenario) -> SchemaPin:
        """The pin of *scenario*'s tables, twins and existing tables as this schema reads
        them."""
        self._require_tables(scenario)
        return SchemaPin.of(
            self._facts,
            self._written_tables(scenario),
            twins=self._twins(scenario),
            existing=[table.name for table in scenario.existing],
            coded=self._coded(scenario),
        )

    def _written(self, scenario: Scenario) -> list[tuple[ObjectRef, TableFacts]]:
        """Each table *scenario*'s run writes into, in insert order, with the facts its
        rows are drawn from: in prep-seed, each table's staging twin after it, since the
        twins' rows are promoted into the final tables."""
        written: list[tuple[ObjectRef, TableFacts]] = []
        for ref in self._facts.insert_order(self._written_tables(scenario)):
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
            self._staging.twin(self._facts.facts_for(table).ref)
            for table in self._written_tables(scenario)
        ]

    def _written_tables(self, scenario: Scenario) -> list[str]:
        """The tables *scenario*'s run writes: those under ``tables:``, then each one's
        translation table."""
        names = [spec.name for spec in scenario.tables]
        return names + [table.table for table in self._translation_tables(scenario)]

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


def _table_notices(
    generator: FakeDataGenerator, scenario: Scenario, translations: Mapping[str, Translation]
) -> Iterator[str]:
    """The run's per-table lines: what it leaves ``NULL``, then the labels its coverage
    leaves out."""
    yield from _left_null_notices(generator, scenario)
    yield from _coverage_notices(scenario, translations)


def _coverage_notices(scenario: Scenario, translations: Mapping[str, Translation]) -> Iterator[str]:
    """A line per translation table whose coverage leaves labels out, in the words of
    confiture's coverage check: ``tl_x: 20 rows × 4 locales, en-GB 4 missing``."""
    counts = {spec.name: spec.count for spec in scenario.tables}
    for entity, translation in translations.items():
        rows = counts[entity]
        codes = [locale.code for locale in translation.locales]
        codes += [translation.pseudo] if translation.pseudo is not None else []
        missing = {
            code: rows - int(rows * translation.coverage.get(code, Fraction(1))) for code in codes
        }
        gaps = [f"{code} {count} missing" for code, count in missing.items() if count]
        if gaps:
            yield (
                f"{translation.table.table}: {rows} rows × {len(codes)} locales, " + ", ".join(gaps)
            )


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


def _qualified(kind: str, name: object) -> None:
    """Refuse *name*, a table a scenario names, unless it carries its schema: tables are
    known by their schema-qualified name, as their codes are (D14)."""
    if not isinstance(name, str) or "." not in name:
        raise ScenarioError(
            f"{kind} {name} is not schema-qualified",
            resolution_hint="Name it with its schema, e.g. catalog.tb_city.",
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
