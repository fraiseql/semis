"""The value a foreign key receives, in the mode a run declares (ARCHITECTURE §5).

Every parent is read from ``ColumnFacts.foreign_key`` — the real foreign key confiture
parsed — and never from a column's name. Pure: facts, the parents' rows and their
learned keys in, values out; no database and no file system.
"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from fraiseql_semis.errors import ResolutionError
from fraiseql_semis.schema import ColumnFacts, ObjectRef, TableFacts


class Resolver(Protocol):
    """What the generator asks for a foreign-key column's value."""

    def value_for(self, column: ColumnFacts, *, table: str) -> object:
        """The value *table*'s FK *column* receives in its next row."""
        ...

    def value_at(self, column: ColumnFacts, *, table: str, row: int) -> object:
        """The value that points *table*'s FK *column* at its parent's *row*-th row."""
        ...


def natural_ids(table: TableFacts, rows: Sequence[Mapping[str, object]]) -> list[object]:
    """The natural id of each of *table*'s *rows*, in order; none when it has no natural id."""
    if table.natural_id is None:
        return []
    return [row[table.natural_id] for row in rows]


class _RoundRobin:
    """Children spread over their parents per child column: child *k* gets parent *k mod n*."""

    def __init__(self) -> None:
        self._parents: dict[str, tuple[TableFacts, list[object]]] = {}
        self._next: dict[tuple[str, str], int] = {}

    def _offer(self, table: TableFacts, values: list[object]) -> None:
        """*values* for *table*'s next rows: a hierarchy offers its rows level by level."""
        _, known = self._parents.setdefault(table.ref.display, (table, []))
        known.extend(values)

    def value_for(self, column: ColumnFacts, *, table: str) -> object:
        values = self._values(column, table)
        turn = self._next.get((table, column.name), 0)
        self._next[(table, column.name)] = turn + 1
        return values[turn % len(values)]

    def value_at(self, column: ColumnFacts, *, table: str, row: int) -> object:
        values = self._values(column, table)
        if row >= len(values):
            raise ResolutionError(
                f"{table}.{column.name} points at row {row + 1} of "
                f"{_parent_of(column, table).display}, which is not remembered yet",
                resolution_hint="Remember each level of a hierarchy before the walk draws the next.",
            )
        return values[row]

    def _values(self, column: ColumnFacts, table: str) -> list[object]:
        """What *column* may point at: its parent's rows so far, refused when there are none."""
        parent = _parent_of(column, table)
        if parent.display not in self._parents:
            raise _no_rows(column, table, parent)
        parent_facts, values = self._parents[parent.display]
        if parent_facts.natural_id is None:
            raise ResolutionError(
                f"{table}.{column.name} references {parent.display}, which shows no natural "
                "id, so semis cannot tell its rows apart",
                resolution_hint="Give the parent a UUID id column, as the trinity pattern does.",
            )
        self._check(column, table, parent_facts)
        if not values:
            raise _no_rows(column, table, parent)
        return values

    def _check(self, column: ColumnFacts, table: str, parent: TableFacts) -> None:
        """Refuse *column* when this mode cannot point it at *parent*."""


class PrepSeedResolver(_RoundRobin):
    """Mode A: a child's FK carries its parent's encoded UUID.

    The project's own resolution functions turn that UUID into the parent's ``pk_*``
    when the row is promoted, so no database is needed.
    """

    def remember(self, table: TableFacts, rows: Sequence[Mapping[str, object]]) -> None:
        """*table*'s rows, generated in this run, as parents later rows may point at."""
        self._offer(table, natural_ids(table, rows))


class ReadBackResolver(_RoundRobin):
    """Mode B: a child's FK carries the ``pk_*`` PostgreSQL gave its parent.

    The keys are learned after the parent's seed is applied, by joining on the UUIDs
    semis wrote, so each child points at a row of this run — never at one guessed.
    """

    def remember(
        self,
        table: TableFacts,
        rows: Sequence[Mapping[str, object]],
        keys: Mapping[object, int],
    ) -> None:
        """*table*'s *rows*, and the ``pk_*`` read back for each row's natural id.

        Every row written must be found: fewer means something else changed them inside
        this run's transaction, and the children are refused rather than spread over
        what remains.
        """
        ids = natural_ids(table, rows)
        found = [keys[uuid] for uuid in ids if uuid in keys]
        if len(found) < len(ids):
            raise ResolutionError(
                f"{table.ref.display}: {len(ids)} rows were written and {len(found)} "
                "were read back",
                resolution_hint=(
                    "Something in this transaction removed or re-identified them — a "
                    "trigger, or a default overwriting the natural id."
                ),
            )
        self._offer(table, list(found))

    def _check(self, column: ColumnFacts, table: str, parent: TableFacts) -> None:
        reference = column.foreign_key
        if reference is not None and reference.column != parent.surrogate_pk:
            raise ResolutionError(
                f"{table}.{column.name} references {parent.ref.display}.{reference.column}, "
                "and read-back learns only a parent's surrogate key "
                f"({parent.surrogate_pk or 'none found'})",
                resolution_hint="Use prep-seed mode, or point the foreign key at the pk_* column.",
            )


def require_parents(
    table: TableFacts, counts: Mapping[str, int], *, left_null: frozenset[str] = frozenset()
) -> None:
    """Refuse *table* when a foreign key's parent draws no rows in a run of *counts*.

    The draw refuses the same column the same way; this says so before a row is drawn.
    A self-reference is a hierarchy's to judge, and a key in *left_null* needs no parent.
    """
    for column in table.columns:
        parent = column.foreign_key.table if column.foreign_key is not None else None
        if (
            parent is not None
            and parent != table.ref
            and column.name not in left_null
            and counts.get(parent.display, 0) < 1
        ):
            raise _no_rows(column, table.ref.display, parent)


def _parent_of(column: ColumnFacts, table: str) -> ObjectRef:
    if column.foreign_key is None:
        raise ValueError(f"{table}.{column.name} is not a foreign key")
    return column.foreign_key.table


def _no_rows(column: ColumnFacts, table: str, parent: ObjectRef) -> ResolutionError:
    return ResolutionError(
        f"{table}.{column.name} references {parent.display}, which has no rows in this run",
        resolution_hint=f"Generate {parent.display} in the same run, with a count of at least one.",
    )
