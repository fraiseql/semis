"""FakeDataGenerator: assembling a row from facts, providers and the UUID encoding.

Pure: facts in, rows out — no database and no file system.
"""

from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Literal, NamedTuple, TypeGuard, cast

from fraiseql_semis.errors import ResolutionError, RowContractError, ScenarioError
from fraiseql_semis.faker_provider import CustomProviderRegistry, FakerProvider
from fraiseql_semis.hierarchy import Hierarchy
from fraiseql_semis.resolution import Resolver, require_parents, unparented
from fraiseql_semis.rows import canonical, declared_length, require_row
from fraiseql_semis.schema import ColumnFacts, ColumnReference, SchemaFacts, TableFacts, qualified
from fraiseql_semis.tenancy import Spread, TenancyRule, Tenants, spread
from fraiseql_semis.translation import (
    PSEUDO_SLOT,
    Locale,
    Translation,
    TranslationTable,
    kept,
    pseudo,
    seed_for,
    sequence,
)
from fraiseql_semis.uuid_generator import SemanticUUIDGenerator

type Row = dict[str, object]

type Override = object
"""A scenario's value for a column: a scalar, a list with one value per row, or a
callable taking the row's 0-based index within its table."""

Fill = frozenset[str] | Literal["all"]
"""The nullable columns a scenario asks drawn, which are otherwise written ``NULL``; or
``all`` of them."""


class Copied(NamedTuple):
    """Where a column's value is copied from: *column* of the row its foreign key *key*
    points at."""

    key: str
    column: str


class _Copy(NamedTuple):
    """A copy checked against the facts: the key the row follows, the parent table it
    points at, and the parent's column copied."""

    key: ColumnFacts
    parent: str
    source: str


@dataclass(frozen=True)
class TablePlan:
    """How a run draws one table: its row count; the ``trusts_trigger`` columns; its
    overridden columns; the nullable columns it asks drawn; a self-referencing table's
    tree; the columns it copies from a parent row; its translation table and locales; and
    the keys that point at a parent of any tenant.
    """

    count: int
    trusted: frozenset[str] = frozenset()
    overrides: Mapping[str, Override] = field(default_factory=dict)
    fill: Fill = frozenset()
    hierarchy: Hierarchy | None = None
    copies: Mapping[str, Copied] = field(default_factory=dict)
    translation: Translation | None = None
    crosses: frozenset[str] = frozenset()


SOFT_DELETE = frozenset({"deleted_at", "deleted_by"})
"""The soft-delete columns, by the trinity convention's names: ``fill: all`` leaves them
``NULL``, so no row it writes arrives deleted."""

_NOT_PLANNED = TablePlan(0)
"""What a run says of a table it has no plan for: a translation table, whose rows follow
its entity's."""


def translations_of(tables: Mapping[str, TablePlan]) -> dict[str, Translation]:
    """Each translated table of *tables*, in their order, with its translation."""
    return {
        table: plan.translation for table, plan in tables.items() if plan.translation is not None
    }


def check_override_lengths(table: str, overrides: Mapping[str, Override], count: int) -> None:
    """Refuse a list override that does not hold exactly one value per row."""
    for column, override in overrides.items():
        if _per_row(override) and len(override) != count:
            raise ScenarioError(
                f"{table}.{column}: the override lists {_counted(len(override), 'value')} "
                f"for {_counted(count, 'row')}",
                resolution_hint="Give one value per row, or a single value for every row.",
            )


def _counted(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _per_row(override: Override) -> TypeGuard[Sequence[object]]:
    return isinstance(override, Sequence) and not isinstance(override, str | bytes)


def _overridden(override: Override, index: int) -> object:
    if _per_row(override):
        return override[index]
    if callable(override):
        return cast("Callable[[int], object]", override)(index)
    return override


class FakeDataGenerator:
    """Rows for a table: the encoded natural id, its slug, and a value per other column.

    The slug in *identifier_column* is a word, then the scenario id and the row's
    sequence in hex, as the UUID carries them, so two scenarios sharing a database never
    collide on it. A column with a default is omitted so PostgreSQL applies it — except
    the natural id and the slug, which are semis' identity for the row. Every row passes
    the row contract before it is returned.
    """

    def __init__(  # noqa: PLR0913 — facts and scenario positional; the rest keyword-only
        self,
        facts: SchemaFacts,
        scenario_id: int,
        *,
        seed: int | None = None,
        locale: str = "en_US",
        providers: CustomProviderRegistry | None = None,
        identifier_column: str = "identifier",
    ) -> None:
        self._facts = facts
        self._uuids = SemanticUUIDGenerator(scenario_id)
        self._scenario_id = scenario_id
        self._seed = seed
        self._registry = providers
        self._values = FakerProvider(locale=locale, seed=seed, registry=providers)
        # Per declared locale code, the provider its translations draw from, made once a
        # run; and the values their unique columns used, shared across the locales.
        self._locale_values: dict[str, FakerProvider] = {}
        self._locale_used: dict[tuple[str, str], set[object]] = {}
        # Per translated table, its rows' sequences, which its translations' derive from.
        self._sequences: dict[str, list[int]] = {}
        self._identifier_column = identifier_column
        # Per table, per unique column: the values this run has emitted.
        self._seen: dict[str, dict[str, set[object]]] = {}
        # Per parent table, per column a child copies: its rows' values, in row order.
        self._kept: dict[str, dict[str, list[object]]] = {}
        # A run's rows over its tenants, and per key and tenant the children so far.
        self._spread: Spread | None = None
        self._turns: dict[tuple[str, str, int], int] = {}

    @property
    def facts(self) -> SchemaFacts:
        """The schema this generator draws rows for."""
        return self._facts

    @property
    def spread(self) -> Spread | None:
        """The last walk's rows over its tenants; ``None`` when it had none."""
        return self._spread

    def walk(
        self,
        tables: Mapping[str, TablePlan],
        *,
        resolver: Resolver | None = None,
        existing: frozenset[str] = frozenset(),
        tenancy: tuple[TenancyRule, Tenants] | None = None,
    ) -> Iterator[tuple[TableFacts, Iterator[Row]]]:
        """Each table of *tables* with its rows, parents before children.

        With *tenancy*, the project's rule and the run's tenants, each tenant-scoped row
        is written its tenant, and its keys into a scoped table point at a parent of
        that tenant (D47).

        A table's translation names its translation table and the locales it is written
        in: one row per row of the table and locale, after it, each drawn in its locale,
        its locale key the resolver's ``locale_key``.

        *tables* maps a qualified table to its ``TablePlan``; *resolver* gives every
        foreign key its value, and *existing* names the tables whose rows are in the
        database already, which the resolver knows. The order is ``dependency_order``'s, taken once, here: a
        cycle raises confiture's ``DependencyCycleError`` before any row is generated. Each
        table's rows are a stream, drawn as the caller reads it, so a caller can apply a
        parent and learn its keys before its children are drawn: it reads each stream to
        its end before asking for the next. A hierarchy is yielded once per level, roots
        first, for the same reason: its children are its own rows.
        """
        translations = translations_of(tables)
        translated = {written.table.table: written for written in translations.values()}
        counts = {
            **{table: plan.count for table, plan in tables.items()},
            **{
                written.table.table: tables[entity].count * len(written.locales)
                for entity, written in translations.items()
            },
        }
        existing = existing | {written.table.locale_table for written in translations.values()}
        self._sequences = {entity: [] for entity in translations}
        order = [qualified(ref) for ref in self._facts.insert_order(counts)]
        resolved: dict[str, dict[str, _Copy]] = {}
        for table in order:
            if table in translated:
                require_parents(self._facts.facts_for(table), counts, existing=existing)
                continue
            plan = tables[table]
            _check_overrides(self._facts.facts_for(table), plan.overrides, plan.count)
            self._check_fill(
                self._facts.facts_for(table),
                plan.fill,
                trusted=plan.trusted,
                hierarchy=plan.hierarchy,
            )
            require_parents(self._facts.facts_for(table), counts, existing=existing)
            resolved[table] = self._check_copies(table, tables, counts, existing)
            _check_hierarchy(
                self._facts.facts_for(table),
                plan.hierarchy,
                plan.count,
                trusted=plan.trusted,
                overrides=plan.overrides,
            )
        self._kept = {}
        for copies_of in resolved.values():
            for copy in copies_of.values():
                self._kept.setdefault(copy.parent, {})[copy.source] = []
        self._spread, self._turns = None, {}
        if tenancy is not None:
            rule, tenants = tenancy
            if tenants.table is not None and tenants.table in tables:
                # The tenants are the rows the run writes there: their ids are its UUIDs.
                code = self._facts.facts_for(tenants.table).table_code
                tenants = replace(
                    tenants,
                    values=[
                        self._uuids.generate(code, sequence=row + 1) for row in range(tenants.count)
                    ],
                )
            self._spread = spread(
                self._facts,
                rule,
                tables,
                order,
                tenants,
                existing=existing,
                translations={
                    written.table.table: entity for entity, written in translations.items()
                },
            )
            tables = self._with_tenants(tables, self._spread)
        for table in order:
            self._require_drawn(table, tables, translated)
        return self._walk(
            order,
            tables,
            unparented={
                table: unparented(self._facts.facts_for(table), counts, existing=existing)
                for table in order
            },
            copies=resolved,
            translations=translated,
            resolver=resolver,
        )

    def _require_drawn(
        self, table: str, tables: Mapping[str, TablePlan], translated: Mapping[str, Translation]
    ) -> None:
        """Refuse a column of *table* the run would draw and semis cannot, before a row is
        drawn."""
        table_facts = self._facts.facts_for(table)
        if table in translated:
            found = self._spread
            tenant = found.columns.get(table) if found is not None else None
            self._drawers(
                self._values,
                table_facts,
                given=frozenset() if tenant is None else frozenset({tenant}),
                omitted=frozenset(),
                left_null=self._left_null(
                    table_facts,
                    {},
                    fill=frozenset({translated[table].table.label}),
                    omitted=frozenset(),
                    unparented=frozenset(),
                ),
                copied=frozenset(),
            )
            return
        plan = tables[table]
        omitted = _omitted(plan.trusted, plan.hierarchy)
        self._drawers(
            self._values,
            table_facts,
            given=plan.overrides.keys(),
            omitted=omitted,
            left_null=self._left_null(
                table_facts,
                plan.overrides,
                fill=plan.fill,
                omitted=omitted,
                unparented=frozenset(),
                copied=frozenset(plan.copies),
            ),
            copied=frozenset(plan.copies),
            fill=plan.fill,
        )

    def _drawers(  # noqa: PLR0913 — whose drawers positionally; which columns by keyword
        self,
        provider: FakerProvider,
        table: TableFacts,
        *,
        given: Collection[str],
        omitted: frozenset[str],
        left_null: frozenset[str],
        copied: frozenset[str],
        fill: Fill = frozenset(),
    ) -> dict[str, Callable[[], object]]:
        """What draws each column of *table* a row takes from a provider: every column
        but the *given*, *omitted*, *left_null* and *copied* ones, a default's unless
        *fill* names it, and those semis writes itself. A column semis cannot draw is
        refused here."""
        display = qualified(table.ref)
        return {
            column.name: provider.drawer(column, table=display)
            for column in table.columns
            if column.name not in given
            and column.name not in omitted | left_null | copied
            and (column.default is None or _named(fill, column.name))
            and self._semis_own(column, table) is None
        }

    @staticmethod
    def _with_tenants(tables: Mapping[str, TablePlan], found: Spread) -> dict[str, TablePlan]:
        """*tables*, each scoped table's tenant column overridden by its rows' tenants'
        values, so it is drawn, checked and reported as an overridden column is."""
        values = found.tenants.values

        def tenant_of(rows: tuple[int, ...]) -> Callable[[int], object]:
            return lambda index: values[rows[index]] if values is not None else None

        return {
            table: (
                replace(
                    plan,
                    overrides={
                        **plan.overrides,
                        found.columns[table]: tenant_of(found.rows[table]),
                    },
                )
                if table in found.columns and table in found.rows
                else plan
            )
            for table, plan in tables.items()
        }

    def _walk(  # noqa: PLR0913 — what walk checked per table, passed through by keyword
        self,
        order: list[str],
        tables: Mapping[str, TablePlan],
        *,
        unparented: Mapping[str, frozenset[str]],
        copies: Mapping[str, Mapping[str, _Copy]],
        translations: Mapping[str, Translation],
        resolver: Resolver | None,
    ) -> Iterator[tuple[TableFacts, Iterator[Row]]]:
        for table in order:
            table_facts = self._facts.facts_for(table)
            if table in translations:
                yield (
                    table_facts,
                    self._translated(
                        table_facts,
                        translations[table],
                        unparented=unparented[table],
                        resolver=resolver,
                    ),
                )
                continue
            plan = tables[table]
            hierarchy, count = plan.hierarchy, plan.count
            levels = [range(count)] if hierarchy is None else hierarchy.levels(count)
            for level in levels:
                yield (
                    table_facts,
                    self._generate(
                        table_facts,
                        level,
                        count,
                        trusted=plan.trusted,
                        overrides=plan.overrides,
                        fill=plan.fill,
                        unparented=unparented[table],
                        hierarchy=hierarchy,
                        copies=copies.get(table, {}),
                        resolver=resolver,
                    ),
                )

    def _translated(
        self,
        table_facts: TableFacts,
        translation: Translation,
        *,
        unparented: frozenset[str],
        resolver: Resolver | None,
    ) -> Iterator[Row]:
        """*translation*'s rows: for each row of its entity, in order, one per locale, in
        ``locales:`` order. Each draws from its locale's provider, reseeded for this
        table, and takes the sequence its entity row's and its slot give."""
        written, display = translation.table, qualified(table_facts.ref)
        columns = {column.name: column for column in table_facts.columns}
        left_null = self._left_null(
            table_facts,
            {},
            fill=frozenset({written.label}),
            omitted=frozenset(),
            unparented=unparented,
        )
        tenant = self._spread.columns.get(display) if self._spread is not None else None
        drawn = []
        for locale in translation.locales:
            provider = self._locale_provider(locale)
            provider.faker.seed_instance(seed_for(self._seed, written.table, locale.code))
            drawers = self._drawers(
                provider,
                table_facts,
                given=frozenset() if tenant is None else frozenset({tenant}),
                omitted=frozenset(),
                left_null=left_null,
                copied=frozenset(),
            )
            drawn.append((locale, provider, drawers))
        return self._draw_translations(
            table_facts,
            written,
            drawn,
            entity_key=columns[written.entity_key],
            left_null=left_null,
            resolver=resolver,
            pseudo_code=translation.pseudo,
            coverage=translation.coverage,
        )

    def _draw_translations(  # noqa: PLR0913 — the table positionally; how it is drawn by keyword
        self,
        table_facts: TableFacts,
        written: TranslationTable,
        drawn: list[tuple[Locale, FakerProvider, Mapping[str, Callable[[], object]]]],
        *,
        entity_key: ColumnFacts,
        left_null: frozenset[str],
        resolver: Resolver | None,
        pseudo_code: str | None = None,
        coverage: Mapping[str, Fraction],
    ) -> Iterator[Row]:
        """Every row is drawn, and a row *coverage* leaves out is then not yielded, so the
        rows written keep the labels and UUIDs they have at full coverage."""
        display = qualified(table_facts.ref)
        seen = self._seen.setdefault(display, {})
        look_up = _resolver(resolver, entity_key, display)
        sources: dict[object, object] = {}
        entities = self._sequences[written.entity]
        codes = [locale.code for locale, _, _ in drawn]
        codes += [pseudo_code] if pseudo_code is not None else []
        written_for = {
            code: kept(entities, share, scenario=self._scenario_id, table=written.table, code=code)
            for code in codes
            if (share := coverage.get(code)) is not None
        }

        def keeps(code: str, entity: int) -> bool:
            return code not in written_for or entity in written_for[code]

        tenant_column = self._spread.columns.get(display) if self._spread is not None else None
        for row_number, entity in enumerate(entities):
            parent = look_up.value_at(entity_key, table=display, row=row_number)
            first: Row = {}
            for slot, (locale, provider, drawers) in enumerate(drawn, start=1):
                given = {
                    written.entity_key: parent,
                    written.locale_key: look_up.locale_key(written.locale_table, locale.code),
                }
                if tenant_column is not None:
                    found = cast("Spread", self._spread)
                    values = cast("Sequence[object]", found.tenants.values)
                    given[tenant_column] = values[found.rows[written.entity][row_number]]
                row = self._row(
                    table_facts,
                    given,
                    None,
                    trusted=frozenset(),
                    left_null=left_null,
                    copied=frozenset(),
                    resolver=resolver,
                    drawers=drawers,
                    words=provider.word,
                    sequence=sequence(entity, slot),
                )
                self._accepted(table_facts, row, trusted=frozenset(), seen=seen)
                first = first or dict(row)
                if keeps(locale.code, entity):
                    yield row
            if pseudo_code is not None:
                row = self._pseudo_row(
                    table_facts,
                    written,
                    first,
                    drawn[0][2],
                    locale_key=look_up.locale_key(written.locale_table, pseudo_code),
                    sequence=sequence(entity, PSEUDO_SLOT),
                    sources=sources,
                )
                self._accepted(table_facts, row, trusted=frozenset(), seen=seen)
                if keeps(pseudo_code, entity):
                    yield row

    def _pseudo_row(  # noqa: PLR0913 — the table and its default row positionally; the rest by keyword
        self,
        table_facts: TableFacts,
        written: TranslationTable,
        default: Row,
        drawers: Mapping[str, Callable[[], object]],
        *,
        locale_key: object,
        sequence: int,
        sources: dict[object, object],
    ) -> Row:
        """The pseudo-locale's row of an entity: its *default* row, the first locale's,
        with its own UUID and slug, the pseudo-locale's key, its label transformed, and
        any other unique column drawn afresh from the first locale's *drawers*.

        A pseudo label has no randomness to draw again: one that another label of the
        table already is, under a unique key, is refused, naming both."""
        display = qualified(table_facts.ref)
        encoded = self._uuids.generate(table_facts.table_code, sequence=sequence)
        fields = SemanticUUIDGenerator.decode(encoded)
        row: Row = {**default, written.locale_key: locale_key}
        natural_id = table_facts.natural_id
        if natural_id is not None and natural_id in row:
            row[natural_id] = encoded
        if self._identifier_column in row:
            word = str(default[self._identifier_column]).rsplit("-", 2)[0]
            row[self._identifier_column] = f"{word}-{fields.scenario_id:x}-{fields.sequence:x}"
        own = {
            table_facts.natural_id,
            self._identifier_column,
            written.entity_key,
            written.locale_key,
        }
        for column in table_facts.columns:
            if column.name == written.label:
                label = pseudo(str(default[written.label]), length=declared_length(column))
                if column.unique:
                    self._unique_pseudo(
                        display, column.name, label, default[written.label], sources
                    )
                row[column.name] = label
            elif column.unique and column.name in drawers and column.name not in own:
                row[column.name] = drawers[column.name]()
        return row

    def _unique_pseudo(
        self, table: str, column: str, label: str, source: object, sources: dict[object, object]
    ) -> None:
        """Refuse *label*, the pseudo label of *source*, when *table*'s unique *column*
        already holds it; else remember it, as a drawn value is."""
        used = self._locale_used.setdefault((table, column), set())
        if label in used:
            other = sources.get(label)
            said = (
                f"the pseudo labels of {other!r} and {source!r} are both {label!r}"
                if other is not None
                else f"the pseudo label of {source!r}, {label!r}, is another row's label"
            )
            raise RowContractError(
                f"{table}.{column}: {said}",
                resolution_hint="Widen the label column, or drop pseudo_locale:.",
            )
        used.add(label)
        sources[label] = source

    def _locale_provider(self, locale: Locale) -> FakerProvider:
        """The provider *locale*'s translations draw from: one per declared code a run,
        sharing the run's registry and one store of used values."""
        found = self._locale_values.get(locale.code)
        if found is None:
            found = self._locale_values[locale.code] = FakerProvider(
                locale=locale.faker, registry=self._registry, used=self._locale_used
            )
        return found

    def generate_rows(  # noqa: PLR0913 — table and count positionally; the rest by keyword
        self,
        table: str,
        count: int,
        *,
        trusted: frozenset[str] = frozenset(),
        overrides: Mapping[str, Override] | None = None,
        fill: Fill = frozenset(),
        resolver: Resolver | None = None,
    ) -> Iterator[Row]:
        """*count* rows for the qualified *table*, each checked against the row contract.

        The rows are a stream, drawn as they are read, so a run holds one at a time. The
        table's arguments are refused here, before the first row is drawn; a row the
        contract or the resolver refuses, as it is drawn. *trusted* names the columns a
        trigger fills (``trusts_trigger``); they are omitted and their absence is not
        refused. *overrides* gives a column the scenario's value in place of a
        provider's, default or not; the natural id and foreign keys cannot be
        overridden. A nullable column is written ``NULL`` unless it is overridden, has
        a provider registered by name, or is named in *fill* (``"all"``: every one). A
        column with a default is left to PostgreSQL unless *fill* lists it.
        A foreign key's value comes from *resolver*, never from a provider;
        without one, a table with a foreign key is refused. A self-referencing table is
        drawn level by level, by ``walk``.
        """
        return self._generate(
            self._facts.facts_for(table),
            range(count),
            count,
            trusted=trusted,
            overrides=overrides or {},
            fill=fill,
            unparented=frozenset(),
            hierarchy=None,
            copies={},
            resolver=resolver,
        )

    def _generate(  # noqa: PLR0913 — which rows positionally; how they are drawn by keyword
        self,
        table_facts: TableFacts,
        indexes: range,
        count: int,
        *,
        trusted: frozenset[str],
        overrides: Mapping[str, Override],
        fill: Fill,
        unparented: frozenset[str],
        hierarchy: Hierarchy | None,
        copies: Mapping[str, _Copy],
        resolver: Resolver | None,
    ) -> Iterator[Row]:
        """The rows numbered *indexes* of *table_facts*' *count*, 0-based within the table.

        Refused here, when called; drawn only as the stream is read.
        """
        _check_overrides(table_facts, overrides, count)
        _check_trusted(table_facts, trusted)
        self._check_fill(table_facts, fill, trusted=trusted, hierarchy=hierarchy)
        _check_hierarchy(table_facts, hierarchy, count, trusted=trusted, overrides=overrides)
        omitted = _omitted(trusted, hierarchy)
        left_null = self._left_null(
            table_facts,
            overrides,
            fill=fill,
            omitted=omitted,
            unparented=unparented,
            copied=frozenset(copies),
        )
        drawers = self._drawers(
            self._values,
            table_facts,
            given=overrides.keys(),
            omitted=omitted,
            left_null=left_null,
            copied=frozenset(copies),
            fill=fill,
        )
        return self._draw(
            table_facts,
            indexes,
            overrides=overrides,
            hierarchy=hierarchy,
            omitted=omitted,
            left_null=left_null,
            copies=copies,
            resolver=resolver,
            drawers=drawers,
        )

    def left_null(  # noqa: PLR0913 — the table positionally; how its run draws it by keyword
        self,
        table: str,
        *,
        trusted: frozenset[str] = frozenset(),
        overrides: Mapping[str, Override] | None = None,
        fill: Fill = frozenset(),
        hierarchy: Hierarchy | None = None,
        counts: Mapping[str, int] | None = None,
        existing: frozenset[str] = frozenset(),
        copies: Mapping[str, Copied] | None = None,
    ) -> tuple[str, ...]:
        """The columns of the qualified *table* a run writes ``NULL`` because nobody names
        them, in the table's order: what a run tells its reader it left empty. With the
        run's *counts* and *existing* tables, a nullable key whose parent has no rows in
        it is one of them; a column in *copies* is named."""
        table_facts = self._facts.facts_for(table)
        left_null = self._left_null(
            table_facts,
            overrides or {},
            fill=fill,
            omitted=_omitted(trusted, hierarchy),
            unparented=(
                frozenset()
                if counts is None
                else unparented(table_facts, counts, existing=existing)
            ),
            copied=frozenset(copies or {}),
        )
        return tuple(column.name for column in table_facts.columns if column.name in left_null)

    def _left_null(  # noqa: PLR0913 — table and overrides positionally; the rest by keyword
        self,
        table: TableFacts,
        overrides: Mapping[str, Override],
        *,
        fill: Fill,
        omitted: frozenset[str],
        unparented: frozenset[str],
        copied: frozenset[str] = frozenset(),
    ) -> frozenset[str]:
        """*table*'s columns written ``NULL``: each nullable value column nobody names,
        and each key in *unparented* neither overridden ``null`` nor omitted.

        A value column is named by an override, a provider registered for it by name, or
        *fill*; ``all`` names every one semis can draw but the soft-delete columns. A
        column in *omitted* is left out of the row. A key with no parent in the run is never drawn, whatever *fill* says.
        """
        keys = unparented - overrides.keys() - omitted
        display = qualified(table.ref)
        return keys | frozenset(
            column.name
            for column in table.columns
            if self._nullable_value(column, table)
            and column.name not in omitted
            and column.name not in overrides
            and column.name not in copied
            and not self._values.names(column, table=display)
            and (
                column.name not in fill
                if fill != "all"
                else column.name in SOFT_DELETE or not self._values.draws(column, table=display)
            )
        )

    def _nullable_value(self, column: ColumnFacts, table: TableFacts) -> bool:
        """Whether *column* is one a run writes ``NULL`` unless the scenario names it: a
        nullable value column with no default."""
        return (
            not column.not_null
            and column.default is None
            and self._semis_own(column, table) is None
        )

    def _fillable(self, column: ColumnFacts, table: TableFacts) -> str | None:
        """Why *column* is not one a scenario chooses to draw; ``None`` when it is: a
        nullable value column, or one with a default, which ``fill:`` draws in place of
        PostgreSQL. A key comes from the run's mode, the natural id and the slug are
        semis' own, and a NOT NULL column with no default is always drawn."""
        own = self._semis_own(column, table)
        if own is not None:
            return own
        if column.not_null and column.default is None:
            return "is NOT NULL with no default, so semis always draws it"
        return None

    def _check_copies(
        self,
        table: str,
        tables: Mapping[str, TablePlan],
        counts: Mapping[str, int],
        existing: frozenset[str],
    ) -> dict[str, _Copy]:
        """*table*'s copies, each with the key column and parent table it follows.

        Refused when the run cannot make one: into a column semis writes itself, through
        a key it cannot follow, of a value it does not know, between two types, or of a
        key left ``NULL`` into a NOT NULL column.
        """
        facts = self._facts.facts_for(table)
        columns = {column.name: column for column in facts.columns}
        plan = tables[table]
        resolved: dict[str, _Copy] = {}
        for name, (key, source) in plan.copies.items():
            column = columns.get(name)
            reason = _left_out(name, column, trusted=plan.trusted, hierarchy=plan.hierarchy)
            if column is None or reason is not None or (own := self._semis_own(column, facts)):
                raise ScenarioError(
                    f"{table}.{name} {reason or own}, so it cannot be copied into",
                    resolution_hint="Copy into a value column semis would otherwise draw.",
                )
            parent = self._followed(facts, key, plan.trusted, counts, existing)
            if isinstance(parent, str):
                raise ScenarioError(
                    f"{table}.{name} copies {key}.{source}, and {parent}",
                    resolution_hint=(
                        "Copy through a foreign key to another table the run writes, under tables:."
                    ),
                )
            origin = next((c for c in parent.columns if c.name == source), None)
            unknown = self._unknown(
                parent, source, origin, tables, unparented(parent, counts, existing=existing)
            )
            if origin is None or unknown is not None:
                raise ScenarioError(
                    f"{table}.{name} copies {qualified(parent.ref)}.{source}, which {unknown}",
                    resolution_hint=(
                        "Copy a column whose value semis knows: the parent's natural id or "
                        "slug, or a column it draws or overrides."
                    ),
                )
            if column.type_key != origin.type_key:
                raise ScenarioError(
                    f"{table}.{name} is {column.type_key}, and {qualified(parent.ref)}.{source} "
                    f"is {origin.type_key}: a copy is not cast",
                    resolution_hint="Copy a column of the same type.",
                )
            left = key in plan.overrides or key in unparented(facts, counts, existing=existing)
            if column.not_null and left:
                raise ScenarioError(
                    f"{table}.{name} is NOT NULL, and copies {key}, which the run leaves NULL",
                    resolution_hint=(
                        f"Give {key} a parent with rows in the run, or copy into a nullable column."
                    ),
                )
            resolved[name] = _Copy(columns[key], qualified(parent.ref), source)
        return resolved

    def _followed(
        self,
        table: TableFacts,
        key: str,
        trusted: frozenset[str],
        counts: Mapping[str, int],
        existing: frozenset[str],
    ) -> TableFacts | str:
        """The table *table*'s foreign key *key* points at, when the run writes it and
        draws the key; otherwise why a copy cannot follow it."""
        column = next((c for c in table.columns if c.name == key), None)
        reference = column.foreign_key if column is not None else None
        parent = qualified(reference.table) if reference is not None else ""
        if column is None or reference is None:
            reason = f"{key} is not a foreign key of {qualified(table.ref)}"
        elif key in trusted:
            reason = f"{key} is trusted to a trigger"
        elif column.default is not None:
            reason = f"{key} has a default, which PostgreSQL applies"
        elif reference.table == table.ref:
            reason = f"{key} references {parent} itself"
        elif parent in existing:
            reason = f"{parent} is under existing:, whose keys semis reads, not its columns"
        elif parent not in counts:
            reason = f"{parent} is not a table the run writes"
        else:
            return self._facts.facts_for(parent)
        return reason

    def _unknown(
        self,
        parent: TableFacts,
        name: str,
        column: ColumnFacts | None,
        tables: Mapping[str, TablePlan],
        parentless: frozenset[str],
    ) -> str | None:
        """Why the run does not know the value of *parent*'s column *name* when its row is
        drawn; ``None`` when it does: the natural id, the slug, an override, a copy, or a
        column semis draws. *parentless* are *parent*'s keys with no parent in the run."""
        plan = tables.get(qualified(parent.ref), _NOT_PLANNED)
        reason = _left_out(name, column, trusted=plan.trusted, hierarchy=plan.hierarchy)
        if (
            reason is not None
            or column is None
            or name in {parent.natural_id, self._identifier_column}
            or name in plan.overrides
            or name in plan.copies
        ):
            return reason
        if column.default is not None and not _named(plan.fill, name):
            return "has a default, which PostgreSQL applies"
        left = self._left_null(
            parent,
            plan.overrides,
            fill=plan.fill,
            omitted=_omitted(plan.trusted, plan.hierarchy),
            unparented=parentless,
            copied=frozenset(plan.copies),
        )
        return "the run leaves NULL" if name in left else None

    def _semis_own(self, column: ColumnFacts, table: TableFacts) -> str | None:
        """Why semis writes *column* itself, whatever the scenario says: the natural id,
        the slug, or a foreign key; ``None`` for any other column."""
        if column.name == table.natural_id:
            return "is the natural id, which carries the encoded UUID"
        if column.name == self._identifier_column:
            return "is the slug, which semis writes from the UUID"
        if column.foreign_key is not None:
            return "is a foreign key, whose value comes from the run's mode"
        return None

    def _check_fill(
        self,
        table: TableFacts,
        fill: Fill,
        *,
        trusted: frozenset[str],
        hierarchy: Hierarchy | None,
    ) -> None:
        """Refuse a *fill* entry that names no column a scenario chooses to draw."""
        if fill == "all":
            return
        columns = {column.name: column for column in table.columns}
        for name in sorted(fill):
            column = columns.get(name)
            reason = _left_out(name, column, trusted=trusted, hierarchy=hierarchy)
            if column is not None and reason is None:
                reason = self._fillable(column, table)
            if reason is not None:
                raise ScenarioError(
                    f"{qualified(table.ref)} fills {name}, which {reason}",
                    resolution_hint=(
                        "Under fill:, name a column semis would otherwise write NULL or "
                        "leave to its default."
                    ),
                )

    def _draw(  # noqa: PLR0913 — which rows positionally; how they are drawn by keyword
        self,
        table_facts: TableFacts,
        indexes: range,
        *,
        overrides: Mapping[str, Override],
        hierarchy: Hierarchy | None,
        omitted: frozenset[str],
        left_null: frozenset[str],
        copies: Mapping[str, _Copy],
        resolver: Resolver | None,
        drawers: Mapping[str, Callable[[], object]],
    ) -> Iterator[Row]:
        seen = self._seen.setdefault(qualified(table_facts.ref), {})
        kept = self._kept.get(qualified(table_facts.ref), {})
        for index in indexes:
            given = {column: _overridden(value, index) for column, value in overrides.items()}
            points_at = None
            if hierarchy is not None and (parent := hierarchy.parent_of(index)) is not None:
                points_at = (hierarchy.parent, parent)
            row = self._row(
                table_facts,
                given,
                points_at,
                trusted=omitted,
                left_null=left_null,
                copied=frozenset(copies),
                resolver=resolver,
                drawers=drawers,
                tenant=self._tenant(qualified(table_facts.ref), index),
            )
            for name, copy in copies.items():
                row[name] = self._copied(qualified(table_facts.ref), row, copy, resolver)
            self._accepted(table_facts, row, trusted=omitted, seen=seen)
            for name, values in kept.items():
                values.append(row.get(name))
            yield row

    @staticmethod
    def _accepted(
        table_facts: TableFacts,
        row: Row,
        *,
        trusted: frozenset[str],
        seen: dict[str, set[object]],
    ) -> None:
        """Refuse *row* when the row contract does, then remember its unique values."""
        require_row(table_facts, row, trusted=trusted, seen=seen)
        for column in table_facts.columns:
            if column.unique and row.get(column.name) is not None:
                seen.setdefault(column.name, set()).add(canonical(row[column.name]))

    def _copied(self, table: str, row: Row, copy: _Copy, resolver: Resolver | None) -> object:
        """*copy*'s parent column in the row *table*'s *row* points at; ``NULL`` when its
        key is."""
        if row.get(copy.key.name) is None:
            return None
        index = _resolver(resolver, copy.key, table).pointed_at(copy.key, table=table)
        return self._kept[copy.parent][copy.source][index]

    def _row(  # noqa: PLR0913 — this row positionally; its table's drawing by keyword
        self,
        table: TableFacts,
        given: Mapping[str, object],
        points_at: tuple[str, int] | None,
        *,
        trusted: frozenset[str],
        left_null: frozenset[str],
        copied: frozenset[str],
        resolver: Resolver | None,
        drawers: Mapping[str, Callable[[], object]],
        words: Callable[[], str] | None = None,
        sequence: int | None = None,
        tenant: int | None = None,
    ) -> Row:
        """One row. *points_at* names a hierarchy's parent column and the row it points at;
        every other self-FK, and a root's parent, is ``NULL``. A column in *copied* holds
        its place, for the caller to copy into once the row's keys are drawn. *drawers*
        draws each column a provider fills, chosen once for the table. A row of *tenant*
        points its keys within it at a parent of that tenant."""
        encoded = self._uuids.generate(table.table_code, sequence=sequence)
        fields = SemanticUUIDGenerator.decode(encoded)
        if qualified(table.ref) in self._sequences:
            self._sequences[qualified(table.ref)].append(fields.sequence)
        word = words or self._values.word
        row: Row = {}
        for column in table.columns:
            if column.name == table.natural_id:
                row[column.name] = encoded
            elif column.name in given:
                row[column.name] = given[column.name]
            elif column.name in copied:
                row[column.name] = None
            elif column.name == self._identifier_column:
                row[column.name] = f"{word()}-{fields.scenario_id:x}-{fields.sequence:x}"
            elif (column.default is not None and column.name not in drawers) or (
                column.name in trusted
            ):
                continue
            elif column.name in left_null:
                row[column.name] = None
            elif _refers_to_itself(column, table):
                row[column.name] = (
                    _resolver(resolver, column, qualified(table.ref)).value_at(
                        column, table=qualified(table.ref), row=points_at[1]
                    )
                    if points_at is not None and points_at[0] == column.name
                    else None
                )
            elif column.foreign_key is not None:
                row[column.name] = self._key(resolver, column, qualified(table.ref), tenant)
            else:
                row[column.name] = drawers[column.name]()
        return row

    def _key(
        self, resolver: Resolver | None, column: ColumnFacts, table: str, tenant: int | None
    ) -> object:
        """The value *table*'s key *column* takes in its next row: round-robin over its
        parent's rows, or, within *tenant*, over that tenant's."""
        asked = _resolver(resolver, column, table)
        found = self._spread
        if tenant is None or found is None or column.name not in found.keys.get(table, ()):
            return asked.value_for(column, table=table)
        parent = qualified(cast("ColumnReference", column.foreign_key).table)
        rows = found.rows_in(parent, tenant)
        turn = self._turns.get((table, column.name, tenant), 0)
        self._turns[table, column.name, tenant] = turn + 1
        return asked.value_at(column, table=table, row=rows[turn % len(rows)])

    def _tenant(self, table: str, index: int) -> int | None:
        """The tenant of *table*'s row *index*; ``None`` when it has none."""
        found = self._spread
        if found is None or table not in found.rows:
            return None
        return found.rows[table][index]


def _left_out(
    name: str, column: ColumnFacts | None, *, trusted: frozenset[str], hierarchy: Hierarchy | None
) -> str | None:
    """Why the column *name* is not in a row semis draws: not a column it writes, trusted
    to a trigger, or the hierarchy's path; ``None`` when it is."""
    if column is None:
        return "is not a column semis writes"
    if name in trusted:
        return "is trusted to a trigger, which fills it"
    if hierarchy is not None and name == hierarchy.path:
        return "is the hierarchy's path, which read-back fills from the keys"
    return None


def _named(fill: Fill, column: str) -> bool:
    """Whether the ``fill:`` list *fill* names *column*; ``all`` names no default."""
    return fill != "all" and column in fill


def _omitted(trusted: frozenset[str], hierarchy: Hierarchy | None) -> frozenset[str]:
    """The columns left out of a row: *trusted*'s, and a hierarchy's path, which read-back
    sets once its keys exist."""
    if hierarchy is None or hierarchy.path is None:
        return trusted
    return trusted | {hierarchy.path}


def _null() -> None:
    """The drawer of a column written ``NULL``."""


def _check_overrides(table: TableFacts, overrides: Mapping[str, Override], count: int) -> None:
    display = qualified(table.ref)
    check_override_lengths(display, overrides, count)
    columns = {column.name: column for column in table.columns}
    for name in overrides:
        column = columns.get(name)
        if column is None:
            reason = "is not a column semis writes"
        elif name == table.natural_id:
            reason = "is the natural id, which carries the encoded UUID"
        elif column.foreign_key is not None and overrides[name] is None:
            _check_left_null(column, table)
            continue
        elif column.foreign_key is not None:
            reason = "is a foreign key, whose value comes from the run's mode"
        else:
            continue
        raise ScenarioError(
            f"{display}.{name} {reason}, so it cannot be overridden",
            resolution_hint=(
                "Override a column the table writes and semis draws a value for; a nullable "
                "foreign key to another table may be overridden null."
            ),
        )


def _check_left_null(column: ColumnFacts, table: TableFacts) -> None:
    """A nullable foreign key may be overridden ``null``: written so, with no parent."""
    if column.not_null:
        raise ScenarioError(
            f"{qualified(table.ref)}.{column.name} is NOT NULL, so it cannot be left null",
            resolution_hint="Generate its parent in the run, and drop the override.",
        )


def _check_trusted(table: TableFacts, trusted: frozenset[str]) -> None:
    unknown = sorted(trusted - {column.name for column in table.columns})
    if unknown:
        raise ScenarioError(
            f"{qualified(table.ref)} trusts {', '.join(unknown)} to a trigger, which is not a "
            "column semis writes",
            resolution_hint="Name, under trusts_trigger, a writable column a trigger fills.",
        )


def _refers_to_itself(column: ColumnFacts, table: TableFacts) -> bool:
    return column.foreign_key is not None and qualified(column.foreign_key.table) == qualified(
        table.ref
    )


def _drawn_self_fks(
    table: TableFacts, overrides: Mapping[str, Override], trusted: frozenset[str]
) -> list[ColumnFacts]:
    """The *table*'s self-FKs a run must draw: every one the scenario neither leaves null
    nor trusts to a trigger."""
    return [
        column
        for column in table.columns
        if _refers_to_itself(column, table)
        and _taken_from_semis(column.name, trusted=trusted, overrides=overrides) is None
    ]


def _taken_from_semis(
    column: str, *, trusted: frozenset[str], overrides: Mapping[str, Override]
) -> tuple[str, str] | None:
    """How the scenario keeps semis from drawing *column*, and the hint for a hierarchy
    whose parent it is: ``None`` when semis draws it."""
    if column in trusted:
        return "trusted to a trigger", (
            f"Drop the hierarchy to leave {column} to the trigger, or drop {column} from "
            "trusts_trigger for a tree."
        )
    if column in overrides and overrides[column] is None:
        return "overridden null", (
            "Drop the hierarchy for a flat set of rows, or the override for a tree."
        )
    return None


def _check_hierarchy(
    table: TableFacts,
    hierarchy: Hierarchy | None,
    count: int,
    *,
    trusted: frozenset[str],
    overrides: Mapping[str, Override],
) -> None:
    """Refuse a self-referencing *table* whose tree the run cannot draw.

    Without a hierarchy, every self-FK must be overridden ``null``, the rows a flat set,
    or trusted to a trigger. With one, only *hierarchy*'s parent column builds the tree: it
    must be one of the table's self-FKs, drawn by semis, and nullable, since a root has no
    parent. Every other self-FK is written
    ``NULL``, so it must be nullable too. A tree of *count* rows with more than its
    roots needs a fan-out. A path column is semis' alone to set.
    """
    display = qualified(table.ref)
    if hierarchy is None:
        drawn = _drawn_self_fks(table, overrides, trusted)
        if drawn:
            raise ScenarioError(
                f"{display}.{drawn[0].name} references its own table, and the run declares no "
                "hierarchy for it",
                resolution_hint=(
                    "Declare hierarchy: {parent, roots, fan_out} on the table, or, for rows "
                    f"that have no parent, override {drawn[0].name} null."
                ),
            )
        return
    own = [column for column in table.columns if _refers_to_itself(column, table)]
    if hierarchy.parent not in {column.name for column in own}:
        raise ScenarioError(
            f"{display}: hierarchy parent {hierarchy.parent} is not a foreign key to {display}",
            resolution_hint="Name, as parent:, the column that references the table's own key.",
        )
    hierarchy.require_fan_out(display, count)
    taken = _taken_from_semis(hierarchy.parent, trusted=trusted, overrides=overrides)
    if taken is not None:
        how, hint = taken
        raise ScenarioError(
            f"{display}.{hierarchy.parent} is the parent of the table's hierarchy, which "
            f"semis draws: it cannot be {how}",
            resolution_hint=hint,
        )
    for column in own:
        if not column.not_null:
            continue
        if column.name == hierarchy.parent:
            reason = "a root has no parent row to point it at"
        else:
            reason = f"only {hierarchy.parent} builds the tree, and semis writes it NULL"
        raise ResolutionError(
            f"{display}.{column.name} is NOT NULL, and {reason}",
            resolution_hint="A self-referencing key must be nullable for semis to draw a tree.",
        )
    if hierarchy.path is not None:
        _check_path(table, hierarchy.path, trusted=trusted, overrides=overrides)


def _check_path(
    table: TableFacts, path: str, *, trusted: frozenset[str], overrides: Mapping[str, Override]
) -> None:
    """Refuse a hierarchy *path* read-back cannot set after the row is inserted."""
    column = next((column for column in table.columns if column.name == path), None)
    if column is None:
        reason = "is not a column semis writes"
    elif column.type_key != "ltree":
        reason = f"is not an ltree column ({column.type_key})"
    elif column.not_null:
        reason = "is NOT NULL, and read-back sets a path only after its row is inserted"
    elif table.natural_id is None or table.surrogate_pk is None:
        reason = (
            "cannot be set: read-back finds a row by its natural id and labels it with its pk_*"
        )
    elif path in overrides or path in trusted:
        reason = "is the hierarchy's path, which semis sets: it cannot be overridden or trusted"
    else:
        return
    raise ScenarioError(
        f"{qualified(table.ref)}.{path} {reason}",
        resolution_hint="Name, as path:, a nullable ltree column the scenario leaves to semis.",
    )


def _resolver(resolver: Resolver | None, column: ColumnFacts, table: str) -> Resolver:
    """*resolver*, or the refusal of a foreign key that has none to ask."""
    if resolver is None:
        raise ResolutionError(
            f"{table}.{column.name} is a foreign key, and this run has no resolver",
            resolution_hint="Generate it in a mode: prep-seed or read-back.",
        )
    return resolver
