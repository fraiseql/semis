"""A run, table by table: generated, written, and — in read-back mode — applied and learned.

The two FK modes of ARCHITECTURE §5 as two functions, so a caller names its mode (D3).
Each writes one seed file per table, parents first, and returns them in that order.
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import replace
from itertools import groupby
from pathlib import Path

from fraiseql_semis import readback, seeds
from fraiseql_semis.errors import ResolutionError
from fraiseql_semis.generator import FakeDataGenerator, Row, TablePlan, translations_of
from fraiseql_semis.hierarchy import Paths, refuse_path_in_prep_seed
from fraiseql_semis.resolution import (
    PrepSeedResolver,
    ReadBackResolver,
    natural_ids,
)
from fraiseql_semis.schema import Connection, SeedFile, TableFacts, qualified
from fraiseql_semis.staging import Staging
from fraiseql_semis.tenancy import TenancyRule, Tenants
from fraiseql_semis.translation import locale_keys, locale_reads


def prep_seed(  # noqa: PLR0913 — three positionals; how the run is written by keyword
    generator: FakeDataGenerator,
    tables: Mapping[str, TablePlan],
    out_dir: Path,
    *,
    format: seeds.Format | None = None,
    staging: Staging | None = None,
    existing: Mapping[str, Sequence[object]] | None = None,
    locales: Mapping[str, Mapping[str, object]] | None = None,
    tenancy: tuple[TenancyRule, Tenants] | None = None,
) -> list[SeedFile]:
    """Mode A: every table written into its staging twin, each child's FK carrying its
    parent's UUID under the twin's ``<fk>_id`` name.

    *existing* maps a table the run does not write to the UUIDs of the rows its children
    point at, as its pin recorded them: offered before the first table is drawn.
    A table's translations are written as in read-back, each row's locale key the UUID
    *locales* gives its code in the locale table. *tenancy* spreads the scoped rows over
    the run's tenants; a tenant table under *existing* gives its UUIDs as their values.

    Rows stream from the walk to the writer, each remembered as it passes, so no
    table's rows are held; a hierarchy's levels are written as one file, roots first.
    Files only: no database is reached, and the output is byte-reproducible.
    """
    staging = staging or Staging()
    for table, plan in tables.items():
        if plan.hierarchy is not None:
            refuse_path_in_prep_seed(table, plan.hierarchy)
    resolver = PrepSeedResolver()
    for name, uuids in (existing or {}).items():
        resolver.existing(generator.facts.keys_for(name), uuids)
    for table, keys in (locales or {}).items():
        resolver.locales(table, keys)
    if tenancy is not None and tenancy[1].table in (existing or {}):
        rule, tenants = tenancy
        tenancy = rule, replace(tenants, values=list((existing or {})[rule.table or ""]))
    written: list[SeedFile] = []
    walk = generator.walk(
        tables, resolver=resolver, existing=frozenset(existing or {}), tenancy=tenancy
    )
    by_table = groupby(walk, key=lambda batch: qualified(batch[0].ref))
    for number, (display, batches) in enumerate(by_table, start=1):
        rows = (row for facts, level in batches for row in _remembered(resolver, facts, level))
        table = generator.facts.facts_for(display)
        twin = staging.twin(table.ref)
        written.append(
            seeds.write(
                out_dir / _file_name(number, twin),
                twin,
                staging.rows(table, rows, generator.facts.columns(twin)),
                facts=generator.facts,
                mode="prep-seed",
                format=format,
            )
        )
    return written


def read_back(  # noqa: PLR0913 — four positionals; how the run is written by keyword
    connection: Connection,
    generator: FakeDataGenerator,
    tables: Mapping[str, TablePlan],
    out_dir: Path,
    *,
    format: seeds.Format | None = None,
    existing: Mapping[str, tuple[str | None, Sequence[str] | None]] | None = None,
    tenancy: tuple[TenancyRule, Tenants] | None = None,
) -> list[SeedFile]:
    """Mode B: apply each table on *connection*, learn its keys, then draw its children.

    A translated table's plan names its translation table and locales: each locale table
    is read by its code before the first table is drawn, and a declared code with no row,
    or two, is refused.

    *existing* maps a table the run does not write to the column naming the rows its
    children point at and their values, or to ``(None, None)`` for every row: their keys are read before the first
    table is drawn. A hierarchy is applied and learned level by level, one file per level
    (``NNN_<table>.L<n>.sql``), because its children are its own rows. Everything runs
    in *connection*'s transaction, which stays the caller's: nothing is committed here,
    so a failure anywhere leaves every table to the caller's rollback. *tenancy* spreads
    the scoped rows over the run's tenants; a tenant table under *existing* is read with
    its natural ids, its tenants' values.
    """
    hierarchies = {
        table: plan.hierarchy for table, plan in tables.items() if plan.hierarchy is not None
    }
    resolver = ReadBackResolver()
    for name, (by, values) in (existing or {}).items():
        keys = generator.facts.keys_for(name)
        if tenancy is None or name != tenancy[1].table:
            resolver.existing(keys, readback.existing_keys(connection, keys, by=by, values=values))
            continue
        rows = readback.existing_rows(
            connection, keys, by=by, values=values, natural_id=keys.natural_id
        )
        resolver.existing(keys, [pk for pk, _, _ in rows])
        tenancy = tenancy[0], replace(tenancy[1], values=[uuid for _, _, uuid in rows])
    for table, (column, codes) in locale_reads(translations_of(tables)).items():
        try:
            rows = readback.existing_rows(
                connection, generator.facts.keys_for(table), by=column, values=None, natural_id=None
            )
        except ResolutionError:
            rows = []  # a locale table holding no row: the first code is refused below
        resolver.locales(
            table,
            locale_keys(((code, pk) for pk, code, _ in rows), codes, table=table, column=column),
        )
    written: list[SeedFile] = []
    levels: dict[str, int] = {}
    paths: dict[str, Paths] = {}
    walk = generator.walk(
        tables, resolver=resolver, existing=frozenset(existing or {}), tenancy=tenancy
    )
    for number, (table, stream) in enumerate(walk, start=1):
        # Held, one table or level at a time: applied, then its keys read back by its ids.
        rows = list(stream)
        display = qualified(table.ref)
        level = None
        if display in hierarchies:
            level = levels[display] = levels.get(display, 0) + 1
        seed = _write(
            generator,
            table,
            rows,
            out_dir=out_dir,
            name=_file_name(number, display, level),
            mode="read-back",
            format=format,
        )
        seeds.apply(connection, [seed.path])
        ids = natural_ids(table, rows)
        keys = {}
        # A table with no surrogate key is no read-back parent: there is no key to learn.
        if table.surrogate_pk is not None:
            keys = readback.learn(connection, table, ids) if ids else {}
            resolver.remember(table, rows, keys)
        hierarchy = hierarchies.get(display)
        # The generator refused a path on a table with no natural id before any row.
        if hierarchy is not None and hierarchy.path is not None and table.natural_id is not None:
            built = paths.setdefault(
                display, Paths(parent=hierarchy.parent, natural_id=table.natural_id)
            )
            readback.set_paths(connection, table, hierarchy.path, built.extend(rows, keys))
        written.append(seed)
    return written


def _remembered(
    resolver: PrepSeedResolver, table: TableFacts, rows: Iterable[Row]
) -> Iterator[Row]:
    """*rows*, each remembered as a parent as it passes, before any later row is drawn."""
    for row in rows:
        resolver.remember(table, [row])
        yield row


def _write(  # noqa: PLR0913 — three positionals; where, how and in which format by keyword
    generator: FakeDataGenerator,
    table: TableFacts,
    rows: list[Row],
    *,
    out_dir: Path,
    name: str,
    mode: seeds.Mode,
    format: seeds.Format | None,
) -> SeedFile:
    """*table*'s rows, written to the seed file *name* in *out_dir*."""
    return seeds.write(
        out_dir / name,
        qualified(table.ref),
        rows,
        facts=generator.facts,
        mode=mode,
        format=format,
    )


def _file_name(number: int, table: str, level: int | None = None) -> str:
    """Numbered in walk order and named by the qualified table, so two schemas' tables
    never collide; a hierarchy's level, when it is written one level to a file."""
    suffix = "" if level is None else f".L{level}"
    return f"{number:03d}_{table}{suffix}.sql"
