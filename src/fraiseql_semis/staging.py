"""The staging twin a prep-seed run writes into (ARCHITECTURE §5).

A project's resolvers read ``<prep_seed_schema>.<table>``: the catalog table's name in
the staging schema, every foreign key a UUID named ``<fk>_id`` — confiture's prep-seed
convention, which its levels 1 and 2 check. A scenario still names the catalog table,
whose foreign keys, constraints and pin semis reads. Pure: facts and rows in, rows out.
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from fraiseql_semis.errors import ScenarioError
from fraiseql_semis.schema import ColumnFacts, ObjectRef, TableFacts


@dataclass(frozen=True)
class Staging:
    """Where a prep-seed run writes: the twins in *schema*."""

    schema: str = "prep_seed"

    def twin(self, table: ObjectRef) -> str:
        """The staging table *table*'s rows are written into."""
        return f"{self.schema}.{table.name}"

    def require(
        self, table: ObjectRef, twin: Sequence[ColumnFacts] | None
    ) -> Sequence[ColumnFacts]:
        """*twin*'s columns, refused when the schema has no twin for *table*."""
        if twin is None:
            name = self.twin(table)
            raise ScenarioError(
                f"{table.display} has no staging twin {name}: a prep-seed run writes there",
                resolution_hint=(
                    f"Declare {name} in the schema, or run the scenario in read-back mode."
                ),
            )
        return twin

    def rows(
        self,
        table: TableFacts,
        rows: Iterable[Mapping[str, object]],
        twin: Sequence[ColumnFacts] | None,
    ) -> Iterator[dict[str, object]]:
        """*rows* as the twin holds them, as they are read: each foreign key under its
        ``_id`` name.

        *twin* is the twin's writable columns; ``None`` when the schema has no twin. A
        twin missing is refused here; a twin missing a column the rows carry, at the
        first row.
        """
        names = {column.name for column in self.require(table.ref, twin)}
        renamed = {
            column.name: f"{column.name}_id"
            for column in table.columns
            if column.foreign_key is not None
        }
        return self._staged(table, rows, names, renamed)

    def _staged(
        self,
        table: TableFacts,
        rows: Iterable[Mapping[str, object]],
        names: set[str],
        renamed: Mapping[str, str],
    ) -> Iterator[dict[str, object]]:
        for number, row in enumerate(rows):
            staged = {renamed.get(name, name): value for name, value in row.items()}
            missing = [name for name in staged if name not in names] if number == 0 else []
            if missing:
                raise ScenarioError(
                    f"{self.twin(table.ref)} has no {', '.join(missing)}, which "
                    f"{table.ref.display}'s rows carry",
                    resolution_hint="Give the twin every column the catalog table is seeded with.",
                )
            yield staged
