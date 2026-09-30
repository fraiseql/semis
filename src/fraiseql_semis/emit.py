"""A run, table by table: generated, written, and — in read-back mode — applied and learned.

The two FK modes of ARCHITECTURE §5 as two functions, so a caller names its mode (D3).
Each writes one seed file per table, parents first, and returns them in that order.
"""

from collections.abc import Iterable, Iterator, Mapping
from itertools import groupby
from pathlib import Path

from fraiseql_semis import readback, seeds
from fraiseql_semis.generator import FakeDataGenerator, Override, Row
from fraiseql_semis.hierarchy import Hierarchy, Paths, refuse_path_in_prep_seed
from fraiseql_semis.resolution import PrepSeedResolver, ReadBackResolver, natural_ids
from fraiseql_semis.schema import Connection, SeedFile, TableFacts
from fraiseql_semis.staging import Staging


def prep_seed(  # noqa: PLR0913 — three positionals; how each table is drawn by keyword
    generator: FakeDataGenerator,
    counts: Mapping[str, int],
    out_dir: Path,
    *,
    trusted: Mapping[str, frozenset[str]] | None = None,
    overrides: Mapping[str, Mapping[str, Override]] | None = None,
    hierarchies: Mapping[str, Hierarchy] | None = None,
    format: seeds.Format | None = None,
    staging: Staging | None = None,
) -> list[SeedFile]:
    """Mode A: every table written into its staging twin, each child's FK carrying its
    parent's UUID under the twin's ``<fk>_id`` name.

    Rows stream from the walk to the writer, each remembered as it passes, so no
    table's rows are held; a hierarchy's levels are written as one file, roots first.
    Files only: no database is reached, and the output is byte-reproducible.
    """
    staging = staging or Staging()
    for table, hierarchy in (hierarchies or {}).items():
        refuse_path_in_prep_seed(table, hierarchy)
    resolver = PrepSeedResolver()
    written: list[SeedFile] = []
    walk = generator.walk(
        counts, trusted=trusted, overrides=overrides, hierarchies=hierarchies, resolver=resolver
    )
    by_table = groupby(walk, key=lambda batch: batch[0].ref.display)
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


def read_back(  # noqa: PLR0913 — four positionals; how each table is drawn by keyword
    connection: Connection,
    generator: FakeDataGenerator,
    counts: Mapping[str, int],
    out_dir: Path,
    *,
    trusted: Mapping[str, frozenset[str]] | None = None,
    overrides: Mapping[str, Mapping[str, Override]] | None = None,
    hierarchies: Mapping[str, Hierarchy] | None = None,
    format: seeds.Format | None = None,
) -> list[SeedFile]:
    """Mode B: apply each table on *connection*, learn its keys, then draw its children.

    A hierarchy is applied and learned level by level, one file per level
    (``NNN_<table>.L<n>.sql``), because its children are its own rows. Everything runs
    in *connection*'s transaction, which stays the caller's: nothing is committed here,
    so a failure anywhere leaves every table to the caller's rollback.
    """
    hierarchies = hierarchies or {}
    resolver = ReadBackResolver()
    written: list[SeedFile] = []
    levels: dict[str, int] = {}
    paths: dict[str, Paths] = {}
    walk = generator.walk(
        counts, trusted=trusted, overrides=overrides, hierarchies=hierarchies, resolver=resolver
    )
    for number, (table, stream) in enumerate(walk, start=1):
        # Held, one table or level at a time: applied, then its keys read back by its ids.
        rows = list(stream)
        display = table.ref.display
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
        keys = readback.learn(connection, table, ids) if ids and table.surrogate_pk else {}
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
        table.ref.display,
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
