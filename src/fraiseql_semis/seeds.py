"""Seed files: semis' rows written, and applied, by confiture.

With ``schema.py``, one of the two modules that call ``confiture.platform`` (D1).
"""

from collections.abc import Iterable, Mapping, Sequence
from itertools import chain
from pathlib import Path
from typing import Literal

from confiture.platform import (
    ApplyResult,
    Connection,
    PrepSeedReport,
    SeedFile,
    apply_seeds,
    validate_seeds,
    writable_columns,
    write_copy_seed,
    write_insert_seed,
)

from fraiseql_semis.schema import SchemaFacts

Mode = Literal["prep-seed", "read-back"]
Format = Literal["copy", "insert"]

# D7: prep-seed writes INSERT, read by level 1 as a COPY seed is (#366, closed in 1.23);
# read-back writes COPY, which the applier streams.
_FORMAT_OF_MODE: dict[Mode, Format] = {"prep-seed": "insert", "read-back": "copy"}
_WRITERS = {"copy": write_copy_seed, "insert": write_insert_seed}


def write(  # noqa: PLR0913 — three positionals; facts, mode and format are keyword-only
    path: Path | str,
    table: str,
    rows: Iterable[Mapping[str, object]],
    *,
    facts: SchemaFacts,
    mode: Mode,
    format: Format | None = None,
) -> SeedFile:
    """Write *rows* into *table*'s seed file at *path*, in *mode*'s format unless given.

    *rows* may be a stream: it is read once, as the writer reads it. The columns are
    the keys of the first row, which are the same for every row of a table; confiture
    refuses a row that differs, and a column PostgreSQL fills.
    """
    writer = _WRITERS[format or _FORMAT_OF_MODE[mode]]
    stream = iter(rows)
    first = next(stream, None)
    if first is None:
        columns = [column.name for column in writable_columns(facts.model, table)]
        return writer(path, table, columns, [], model=facts.model)
    return writer(path, table, list(first), chain([first], stream), model=facts.model)


def apply(database: str | Connection, paths: Sequence[Path | str]) -> ApplyResult:
    """Apply the seed files at *paths*, in order.

    Always an explicit list, never a directory (D8): what is applied is exactly what
    semis wrote, in the order it wrote it, whatever else the directory holds. Given a
    connection, the transaction stays the caller's.
    """
    if isinstance(paths, str | Path):
        raise TypeError("seeds.apply takes a list of seed files, not one path")
    for path in paths:
        if Path(path).is_dir():
            raise ValueError(
                f"{path} is a directory: pass the seed files themselves, so what is "
                "applied is exactly what was written"
            )
    return apply_seeds(database, list(paths))


def validate(  # noqa: PLR0913 — one positional; the rest are validate_seeds' keywords
    seeds_dir: Path | str,
    *,
    schema_dir: Path | str,
    max_level: int = 3,
    database: str | Connection | None = None,
    prep_seed_schema: str = "prep_seed",
    catalog_schema: str | None = None,
) -> PrepSeedReport:
    """Run confiture's prep-seed levels 1 to *max_level* over the seeds in *seeds_dir*.

    Levels 4 and 5 load the seeds and run the resolvers against *database*, and roll
    back: a URL's connection is confiture's, a caller's runs inside a savepoint.
    *catalog_schema* is passed only when given: confiture takes each staging table's
    final table from its resolver, and the catalog schema is its last fallback (#458).
    """
    fallback = {} if catalog_schema is None else {"catalog_schema": catalog_schema}
    return validate_seeds(
        seeds_dir,
        schema_dir=schema_dir,
        max_level=max_level,
        database=database,
        prep_seed_schema=prep_seed_schema,
        **fallback,
    )
