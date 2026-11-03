"""Tenants: which tables a run spreads over them, and each row's tenant (ARCHITECTURE §5).

Pure: the project's rule, the facts and a run's plans in, each scoped table's rows'
tenants out; no database and no file system.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Protocol
from uuid import UUID

from fraiseql_semis.errors import ResolutionError, ScenarioError
from fraiseql_semis.hierarchy import Hierarchy
from fraiseql_semis.schema import ColumnFacts, SchemaFacts


@dataclass(frozen=True)
class TenancyRule:
    """How a project marks its tenant-scoped tables, stated once in ``semis.yaml`` (D46).

    A table is scoped when it has *column*, unless *exclude* names it; *include* maps a
    table whose tenant column is named otherwise to that name. *table*, when given, is
    the tenant table: its rows are the tenants, and the tenant column holds a tenant's
    natural id.
    """

    column: str
    table: str | None = None
    exclude: frozenset[str] = frozenset()
    include: Mapping[str, str] = field(default_factory=dict)

    def column_of(self, table: str) -> str | None:
        """The tenant column *table* is scoped by, if it has one by this rule: ``None``
        for an excluded table."""
        if table in self.exclude:
            return None
        return self.include.get(table, self.column)


@dataclass(frozen=True)
class Tenants:
    """A run's tenants: how many, each one's value in the tenant column when known, and
    the tenant table they are the rows of, when it is in the run."""

    count: int
    values: Sequence[object] | None = None
    table: str | None = None

    def named(self, tenant: int) -> str:
        """*tenant* as a refusal names it: its value, or its place among the tenants."""
        if self.values is not None:
            return str(self.values[tenant])
        return f"#{tenant + 1} of {self.count}"


class Planned(Protocol):
    """What the spread reads of a table's plan."""

    @property
    def count(self) -> int: ...
    @property
    def trusted(self) -> frozenset[str]: ...
    @property
    def overrides(self) -> Mapping[str, object]: ...
    @property
    def fill(self) -> frozenset[str] | str: ...
    @property
    def hierarchy(self) -> Hierarchy | None: ...
    @property
    def copies(self) -> Mapping[str, object]: ...
    @property
    def crosses(self) -> frozenset[str]: ...


@dataclass(frozen=True)
class Spread:
    """A run's rows over its tenants (D47).

    *rows* gives each scoped table's rows their tenant, by position; *columns* names the
    tenant column a scoped table's rows are written their tenant's value in; *keys*
    names, per scoped table, the foreign keys that point at a parent of the row's own
    tenant.
    """

    tenants: Tenants
    rows: Mapping[str, tuple[int, ...]]
    columns: Mapping[str, str]
    keys: Mapping[str, frozenset[str]]

    def __post_init__(self) -> None:
        # Each parent's rows per tenant, found once: a child asks for them per row.
        object.__setattr__(self, "_in", cache(self._rows_in))

    def rows_in(self, table: str, tenant: int) -> tuple[int, ...]:
        """*table*'s rows, by position, of *tenant*."""
        return self._in(table, tenant)  # ty: ignore[unresolved-attribute]

    def _rows_in(self, table: str, tenant: int) -> tuple[int, ...]:
        return tuple(row for row, owner in enumerate(self.rows[table]) if owner == tenant)

    @property
    def scoped(self) -> tuple[str, ...]:
        """The scoped tables, in the run's order."""
        return tuple(self.rows)


def spread(  # noqa: PLR0913 — the facts and the run positionally; what it reads by keyword
    facts: SchemaFacts,
    rule: TenancyRule,
    tables: Mapping[str, Planned],
    order: Sequence[str],
    tenants: Tenants,
    *,
    existing: frozenset[str] = frozenset(),
    translations: Mapping[str, str] | None = None,
) -> Spread:
    """Which of *tables*, in insert *order*, are scoped, and each scoped row's tenant.

    *translations* maps a translation table to its entity: its rows follow the entity's,
    a locale at a time. Refused before a row is drawn: a key whose parent has no row in
    a tenant the child has rows in, a key into an ``existing:`` table holding the tenant
    column, a tenant column the scenario names another way, and a crossing that is not.
    """
    translations = translations or {}
    rows: dict[str, tuple[int, ...]] = {}
    columns: dict[str, str] = {}
    keys: dict[str, frozenset[str]] = {}
    if tenants.table is not None and tenants.table in existing:
        rows[tenants.table] = tuple(range(tenants.count))
    for table in order:
        if table in translations:
            entity = translations[table]
            column = _value_column(facts, rule, table, tenants)
            if entity in rows and column is not None:
                columns[table] = column
            continue
        plan = tables[table]
        if table == tenants.table:
            # The tenant table is the dimension: its row i is tenant i, and no key of it
            # stays within a tenant.
            rows[table] = tuple(range(plan.count))
            keys[table] = frozenset()
            continue
        if table in rule.exclude:
            continue
        within = _within(
            facts,
            table,
            plan,
            rule=rule,
            scoped=rows,
            existing=existing,
            tenants=tenants,
        )
        column = _value_column(facts, rule, table, tenants)
        if column is None and not within:
            continue
        if column is not None:
            _check_column(table, column, plan)
            _check_values(facts, table, column, tenants)
            columns[table] = column
        keys[table] = within
        rows[table] = _tenants_of(plan, tenants.count)
        for key in sorted(within):
            _require_parents(facts, table, key, rows, tenants)
    return Spread(tenants, rows, columns, keys)


def _within(  # noqa: PLR0913 — the table and its plan, then what is known of the run
    facts: SchemaFacts,
    table: str,
    plan: Planned,
    *,
    rule: TenancyRule,
    scoped: Mapping[str, tuple[int, ...]],
    existing: frozenset[str],
    tenants: Tenants,
) -> frozenset[str]:
    """*table*'s keys a scoped row points within its tenant: each drawn key into a scoped
    table, but those *plan* crosses tenants by."""
    within = set()
    crossable = set()
    for column in facts.facts_for(table).columns:
        parent = column.foreign_key.table.display if column.foreign_key is not None else None
        if parent is None or parent == table or not _drawn(column, plan):
            continue
        if parent in scoped:
            crossable.add(column.name)
            if column.name not in plan.crosses:
                within.add(column.name)
        elif parent in existing and _scoped_existing(facts, rule, parent, tenants):
            crossable.add(column.name)
            if column.name not in plan.crosses:
                raise ScenarioError(
                    f"{table}.{column.name} points at {parent}, under existing:, whose rows' "
                    "tenants semis does not read",
                    resolution_hint=(
                        f"Name {column.name} under crosses_tenants:, or exclude {parent} in "
                        "semis.yaml's tenancy:."
                    ),
                )
    stray = sorted(plan.crosses - crossable)
    if stray:
        raise ScenarioError(
            f"{table} crosses tenants by {stray[0]}, which is no key to a tenant-scoped table",
            resolution_hint="Name, under crosses_tenants:, a key into a tenant-scoped table.",
        )
    return frozenset(within)


def _drawn(column: ColumnFacts, plan: Planned) -> bool:
    """Whether the run draws *column*, a key: not left ``NULL`` by an override, not
    trusted to a trigger, not left to its default."""
    return (
        column.default is None
        and column.name not in plan.trusted
        and not (column.name in plan.overrides and plan.overrides[column.name] is None)
    )


def _scoped_existing(facts: SchemaFacts, rule: TenancyRule, table: str, tenants: Tenants) -> bool:
    """Whether *table*, under ``existing:``, holds rows of some tenant: it has the tenant
    column, and is not the tenant table."""
    column = rule.column_of(table)
    names = {found.name for found in facts.columns(table) or ()}
    return table != tenants.table and column is not None and column in names


def _value_column(
    facts: SchemaFacts, rule: TenancyRule, table: str, tenants: Tenants
) -> str | None:
    """*table*'s tenant column, when the run writes it its tenant's value: a plain
    column. One that is a foreign key is resolved as a key into the tenant table."""
    name = rule.column_of(table)
    column = next((c for c in facts.facts_for(table).columns if c.name == name), None)
    if column is None:
        return None
    reference = column.foreign_key
    if reference is None:
        return column.name
    if reference.table.display != tenants.table:
        raise ScenarioError(
            f"{table}.{column.name} is the tenant column, and a foreign key to "
            f"{reference.table.display}, which is not the run's tenant table",
            resolution_hint=(
                f"Name {reference.table.display} as tenancy: table: in semis.yaml, and write "
                "it in the run or list its tenants under existing:."
            ),
        )
    return None


def _check_column(table: str, column: str, plan: Planned) -> None:
    """Refuse a tenant column the scenario names another way."""
    fill = plan.fill if isinstance(plan.fill, frozenset) else frozenset()
    named = (
        ("overridden", plan.overrides),
        ("trusted to a trigger", plan.trusted),
        ("copied", plan.copies),
        ("drawn under fill:", fill),
    )
    how = next((how for how, names in named if column in names), None)
    if how is not None:
        raise ScenarioError(
            f"{table}.{column} is the tenant column, which the run's tenants fill: it "
            f"cannot be {how}",
            resolution_hint=f"Drop {column} from the table's entry; each row takes its tenant.",
        )


def _check_values(facts: SchemaFacts, table: str, column: str, tenants: Tenants) -> None:
    """Refuse a tenant's value the tenant column cannot hold: a uuid that is not one."""
    found = next(c for c in facts.facts_for(table).columns if c.name == column)
    if found.type_key != "uuid" or tenants.values is None or tenants.table is not None:
        return
    for value in tenants.values:
        try:
            UUID(str(value))
        except ValueError:
            raise ScenarioError(
                f"tenant {value!r} is no uuid, which {table}.{column} holds",
                resolution_hint="List under tenants: the values the tenant column holds.",
            ) from None


def _tenants_of(plan: Planned, count: int) -> tuple[int, ...]:
    """Each of a scoped table's rows' tenant: round-robin, but a tree's child takes its
    parent's, so a tree stays in its root's tenant."""
    hierarchy = plan.hierarchy
    found: list[int] = []
    for row in range(plan.count):
        parent = hierarchy.parent_of(row) if hierarchy is not None else None
        found.append(found[parent] if parent is not None else row % count)
    return tuple(found)


def _require_parents(
    facts: SchemaFacts,
    table: str,
    key: str,
    rows: Mapping[str, tuple[int, ...]],
    tenants: Tenants,
) -> None:
    """Refuse *table*'s *key* when its parent has rows in the run and none in a tenant
    *table* has rows in. A parent with no rows at all is the run's to leave ``NULL``."""
    column = next(c for c in facts.facts_for(table).columns if c.name == key)
    parent = column.foreign_key.table.display if column.foreign_key is not None else ""
    held = set(rows[parent])
    if not held:
        return
    missing = next((tenant for tenant in rows[table] if tenant not in held), None)
    if missing is not None:
        raise ResolutionError(
            f"{table}.{key} points at {parent}'s rows of its own tenant, and {parent} has "
            f"none of tenant {tenants.named(missing)}",
            resolution_hint=(
                f"Give {parent} a row in every tenant (a count of at least {tenants.count}), "
                f"or name {key} under crosses_tenants:."
            ),
        )
