"""FakeDataGenerator: assembling a row from facts, providers and the UUID encoding.

Pure: facts in, rows out — no database and no file system.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import TypeGuard, cast

from fraiseql_semis.errors import ResolutionError, ScenarioError
from fraiseql_semis.faker_provider import CustomProviderRegistry, FakerProvider
from fraiseql_semis.hierarchy import Hierarchy
from fraiseql_semis.resolution import Resolver, require_parents
from fraiseql_semis.rows import require_row
from fraiseql_semis.schema import ColumnFacts, SchemaFacts, TableFacts
from fraiseql_semis.uuid_generator import SemanticUUIDGenerator

Row = dict[str, object]

Override = object
"""A scenario's value for a column: a scalar, a list with one value per row, or a
callable taking the row's 0-based index within its table."""


def check_override_lengths(table: str, overrides: Mapping[str, Override], count: int) -> None:
    """Refuse a list override that does not hold exactly one value per row."""
    for column, override in overrides.items():
        if _per_row(override) and len(override) != count:
            raise ScenarioError(
                f"{table}.{column}: the override lists {len(override)} values for {count} rows",
                resolution_hint="Give one value per row, or a single value for every row.",
            )


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
        self._values = FakerProvider(locale=locale, seed=seed, registry=providers)
        self._identifier_column = identifier_column
        # Per table, per unique column: the values this run has emitted.
        self._seen: dict[str, dict[str, set[object]]] = {}

    @property
    def facts(self) -> SchemaFacts:
        """The schema this generator draws rows for."""
        return self._facts

    def walk(
        self,
        counts: Mapping[str, int],
        *,
        trusted: Mapping[str, frozenset[str]] | None = None,
        overrides: Mapping[str, Mapping[str, Override]] | None = None,
        hierarchies: Mapping[str, Hierarchy] | None = None,
        resolver: Resolver | None = None,
    ) -> Iterator[tuple[TableFacts, Iterator[Row]]]:
        """Each table of *counts* with its rows, parents before children.

        *counts* maps a qualified table to its row count; *trusted* maps a table to its
        ``trusts_trigger`` columns, *overrides* to its overridden columns, and
        *hierarchies* a self-referencing table to its tree; *resolver* gives every
        foreign key its value. The order is ``dependency_order``'s, taken once, here: a
        cycle raises confiture's ``DependencyCycleError`` before any row is generated. Each
        table's rows are a stream, drawn as the caller reads it, so a caller can apply a
        parent and learn its keys before its children are drawn: it reads each stream to
        its end before asking for the next. A hierarchy is yielded once per level, roots
        first, for the same reason: its children are its own rows.
        """
        order = [ref.display for ref in self._facts.insert_order(counts)]
        hierarchies = hierarchies or {}
        for table in order:
            given = (overrides or {}).get(table, {})
            _check_overrides(self._facts.facts_for(table), given, counts[table])
            left_null = frozenset(column for column, value in given.items() if value is None)
            require_parents(self._facts.facts_for(table), counts, left_null=left_null)
            _check_hierarchy(
                self._facts.facts_for(table),
                hierarchies.get(table),
                trusted=(trusted or {}).get(table, frozenset()),
                overrides=(overrides or {}).get(table, {}),
            )
        return self._walk(
            order,
            counts,
            trusted=trusted or {},
            overrides=overrides or {},
            hierarchies=hierarchies,
            resolver=resolver,
        )

    def _walk(  # noqa: PLR0913 — the walk's per-table mappings, passed through by keyword
        self,
        order: list[str],
        counts: Mapping[str, int],
        *,
        trusted: Mapping[str, frozenset[str]],
        overrides: Mapping[str, Mapping[str, Override]],
        hierarchies: Mapping[str, Hierarchy],
        resolver: Resolver | None,
    ) -> Iterator[tuple[TableFacts, Iterator[Row]]]:
        for table in order:
            table_facts = self._facts.facts_for(table)
            hierarchy = hierarchies.get(table)
            count = counts[table]
            levels = [range(count)] if hierarchy is None else hierarchy.levels(count)
            for level in levels:
                yield (
                    table_facts,
                    self._generate(
                        table_facts,
                        level,
                        count,
                        trusted=trusted.get(table, frozenset()),
                        overrides=overrides.get(table, {}),
                        hierarchy=hierarchy,
                        resolver=resolver,
                    ),
                )

    def generate_rows(
        self,
        table: str,
        count: int,
        *,
        trusted: frozenset[str] = frozenset(),
        overrides: Mapping[str, Override] | None = None,
        resolver: Resolver | None = None,
    ) -> Iterator[Row]:
        """*count* rows for the qualified *table*, each checked against the row contract.

        The rows are a stream, drawn as they are read, so a run holds one at a time. The
        table's arguments are refused here, before the first row is drawn; a row the
        contract or the resolver refuses, as it is drawn. *trusted* names the columns a
        trigger fills (``trusts_trigger``); they are omitted and their absence is not
        refused. *overrides* gives a column the scenario's value in place of a
        provider's, default or not; the natural id and foreign keys cannot be
        overridden. A foreign key's value comes from *resolver*, never from a provider;
        without one, a table with a foreign key is refused. A self-referencing table is
        drawn level by level, by ``walk``.
        """
        return self._generate(
            self._facts.facts_for(table),
            range(count),
            count,
            trusted=trusted,
            overrides=overrides or {},
            hierarchy=None,
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
        hierarchy: Hierarchy | None,
        resolver: Resolver | None,
    ) -> Iterator[Row]:
        """The rows numbered *indexes* of *table_facts*' *count*, 0-based within the table.

        Refused here, when called; drawn only as the stream is read.
        """
        _check_overrides(table_facts, overrides, count)
        _check_trusted(table_facts, trusted)
        _check_hierarchy(table_facts, hierarchy, trusted=trusted, overrides=overrides)
        # A hierarchy's path is set by read-back once its keys exist, never drawn.
        omitted = (
            trusted if hierarchy is None or hierarchy.path is None else (trusted | {hierarchy.path})
        )
        drawers = {
            column.name: self._values.drawer(column, table=table_facts.ref.display)
            for column in table_facts.columns
        }
        return self._draw(
            table_facts,
            indexes,
            overrides=overrides,
            hierarchy=hierarchy,
            omitted=omitted,
            resolver=resolver,
            drawers=drawers,
        )

    def _draw(  # noqa: PLR0913 — which rows positionally; how they are drawn by keyword
        self,
        table_facts: TableFacts,
        indexes: range,
        *,
        overrides: Mapping[str, Override],
        hierarchy: Hierarchy | None,
        omitted: frozenset[str],
        resolver: Resolver | None,
        drawers: Mapping[str, Callable[[], object]],
    ) -> Iterator[Row]:
        seen = self._seen.setdefault(table_facts.ref.display, {})
        for index in indexes:
            given = {column: _overridden(value, index) for column, value in overrides.items()}
            points_at = None
            if hierarchy is not None and (parent := hierarchy.parent_of(index)) is not None:
                points_at = (hierarchy.parent, parent)
            row = self._row(
                table_facts, given, points_at, trusted=omitted, resolver=resolver, drawers=drawers
            )
            require_row(table_facts, row, trusted=omitted, seen=seen)
            for column in table_facts.columns:
                if column.unique and row.get(column.name) is not None:
                    seen.setdefault(column.name, set()).add(row[column.name])
            yield row

    def _row(  # noqa: PLR0913 — this row positionally; its table's drawing by keyword
        self,
        table: TableFacts,
        given: Mapping[str, object],
        points_at: tuple[str, int] | None,
        *,
        trusted: frozenset[str],
        resolver: Resolver | None,
        drawers: Mapping[str, Callable[[], object]],
    ) -> Row:
        """One row. *points_at* names a hierarchy's parent column and the row it points at;
        every other self-FK, and a root's parent, is ``NULL``. *drawers* draws each column
        a provider fills, chosen once for the table."""
        encoded = self._uuids.generate(table.table_code)
        fields = SemanticUUIDGenerator.decode(encoded)
        row: Row = {}
        for column in table.columns:
            if column.name == table.natural_id:
                row[column.name] = encoded
            elif column.name in given:
                row[column.name] = given[column.name]
            elif column.name == self._identifier_column:
                row[column.name] = (
                    f"{self._values.word()}-{fields.scenario_id:x}-{fields.sequence:x}"
                )
            elif column.default is not None or column.name in trusted:
                continue
            elif _refers_to_itself(column, table):
                row[column.name] = (
                    _resolver(resolver, column, table.ref.display).value_at(
                        column, table=table.ref.display, row=points_at[1]
                    )
                    if points_at is not None and points_at[0] == column.name
                    else None
                )
            elif column.foreign_key is not None:
                row[column.name] = _resolver(resolver, column, table.ref.display).value_for(
                    column, table=table.ref.display
                )
            else:
                row[column.name] = drawers[column.name]()
        return row


def _check_overrides(table: TableFacts, overrides: Mapping[str, Override], count: int) -> None:
    display = table.ref.display
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
    """A nullable key to another table may be overridden ``null``: written so, no parent."""
    if column.not_null:
        reason, hint = "is NOT NULL", "Generate its parent in the run, and drop the override."
    elif _refers_to_itself(column, table):
        reason, hint = "references its own table", "Its hierarchy draws it; drop the override."
    else:
        return
    raise ScenarioError(
        f"{table.ref.display}.{column.name} {reason}, so it cannot be left null",
        resolution_hint=hint,
    )


def _check_trusted(table: TableFacts, trusted: frozenset[str]) -> None:
    unknown = sorted(trusted - {column.name for column in table.columns})
    if unknown:
        raise ScenarioError(
            f"{table.ref.display} trusts {', '.join(unknown)} to a trigger, which is not a "
            "column semis writes",
            resolution_hint="Name, under trusts_trigger, a writable column a trigger fills.",
        )


def _refers_to_itself(column: ColumnFacts, table: TableFacts) -> bool:
    return column.foreign_key is not None and column.foreign_key.table.display == table.ref.display


def _check_hierarchy(
    table: TableFacts,
    hierarchy: Hierarchy | None,
    *,
    trusted: frozenset[str],
    overrides: Mapping[str, Override],
) -> None:
    """Refuse a self-referencing *table* whose tree the run cannot draw.

    Only *hierarchy*'s parent column builds the tree: it must be one of the table's
    self-FKs, and nullable, since a root has no parent. Every other self-FK is written
    ``NULL``, so it must be nullable too. A path column is semis' alone to set.
    """
    display = table.ref.display
    own = [column for column in table.columns if _refers_to_itself(column, table)]
    if hierarchy is None:
        if own:
            raise ScenarioError(
                f"{display}.{own[0].name} references its own table, and the run declares no "
                "hierarchy for it",
                resolution_hint="Declare hierarchy: {parent, roots, fan_out} on the table.",
            )
        return
    if hierarchy.parent not in {column.name for column in own}:
        raise ScenarioError(
            f"{display}: hierarchy parent {hierarchy.parent} is not a foreign key to {display}",
            resolution_hint="Name, as parent:, the column that references the table's own key.",
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
        f"{table.ref.display}.{path} {reason}",
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
