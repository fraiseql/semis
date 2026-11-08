"""Which views a scenario's rows reach, and what each holds (fraiseql/semis#9, D56).

A view is reached when it reads a table the scenario writes, directly or through other
views, materialized views or TVIEWs, as confiture's ``view_reads`` follows them; a
routine's body is not followed. A view is multi-tenant when it outputs the project's
tenant column, by confiture's ``view_columns``. The counts are ``readback``'s.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from fraiseql_semis import readback
from fraiseql_semis.schema import Connection, ObjectRef, SchemaFacts, qualified

_TWO = 2  # fewer tenants than this, and a view cannot show one tenant's rows hidden


@dataclass(frozen=True)
class ViewCount:
    """What one view holds: its *rows*, and, when it outputs the tenant column, how
    many distinct *tenants* they belong to; ``None`` otherwise. *kind* is confiture's:
    ``view``, ``matview`` or ``tview``."""

    view: str
    kind: str
    rows: int
    tenants: int | None

    @property
    def single_tenant(self) -> bool:
        """A multi-tenant view whose rows are fewer than two tenants': a test of tenant
        isolation over it has no other tenant's rows to hide."""
        return self.tenants is not None and self.tenants < _TWO

    @property
    def mark(self) -> str:
        """What the report says beside the count: ``no rows``, ``no tenant`` (rows whose
        tenant column is ``NULL``), ``one tenant``, or nothing."""
        if not self.rows:
            return "no rows"
        if self.tenants == 0:
            return "no tenant"
        if self.tenants == 1:
            return "one tenant"
        return ""


def reached(facts: SchemaFacts, tables: Iterable[str]) -> list[ObjectRef]:
    """The views that read one of *tables*, schema-qualified names the model holds, in
    the order of their names."""
    written = {ref.identity for table in tables if (ref := facts.ref(table)) is not None}
    return [view for view in facts.views() if facts.reads(view) & written]


def count(
    facts: SchemaFacts,
    tables: Iterable[str],
    *,
    tenant_column: str | None,
    connection: Connection,
) -> tuple[ViewCount, ...]:
    """Each view *tables* reach, counted on *connection*: its rows, and its tenants
    when it outputs *tenant_column*. Without a tenant column, no view's columns are
    asked for."""
    counts: list[ViewCount] = []
    for view in reached(facts, tables):
        column = tenant_column
        if column is not None and column not in facts.view_columns(view):
            column = None
        rows, tenants = readback.count_view(connection, view, column)
        counts.append(ViewCount(qualified(view), view.kind, rows, tenants))
    return tuple(counts)
