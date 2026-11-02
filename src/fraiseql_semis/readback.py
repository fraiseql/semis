"""Learning a parent's ``pk_*`` after its seed is applied (read-back mode, ARCHITECTURE §5).

The one module that imports ``psycopg``, and the only SQL semis writes (D9): taking a
scenario's lock, asking whether its rows are already applied, deleting them for a reset,
reading the keys of rows a run did not write, learning keys, and setting a hierarchy's
paths from them. Their identifiers come from the model, composed with
``psycopg.sql.Identifier``, and their values are parameters.
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import NamedTuple, cast

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from fraiseql_semis.errors import ResolutionError, SchemaNotBuiltError, UnreachableDatabaseError
from fraiseql_semis.schema import Connection, ObjectRef, Reference, TableFacts, TableKeys

SEMIS_LOCK_CLASS = 0x5E3115
"""semis' advisory-lock class: the first of the two ``int4`` keys of every lock it takes,
so its keys cannot meet another application's single-``bigint`` ones (ARCHITECTURE D37)."""

_LOCK = sql.SQL("SELECT pg_advisory_xact_lock(%s, %s)")
_HOLDS = sql.SQL("SELECT EXISTS (SELECT FROM {schema}.{table} WHERE {id} BETWEEN %s AND %s)")
_DELETE = sql.SQL("DELETE FROM {schema}.{table} WHERE {id} BETWEEN %s AND %s")
_DELETE_THROUGH = sql.SQL(
    "DELETE FROM {schema}.{table} AS t USING {parent_schema}.{parent} AS p"
    " WHERE t.{key} = p.{column} AND p.{id} BETWEEN %s AND %s"
)
_COUNT = sql.SQL("SELECT count(*) FROM {schema}.{table}")
_LOCK_SHARED = sql.SQL("LOCK TABLE {tables} IN SHARE MODE")
_POINTING = sql.SQL(
    "SELECT count(*) FROM {schema}.{table} AS r WHERE EXISTS (SELECT FROM {target_schema}."
    "{target} AS t WHERE {joined} AND {scoped})"
)
_IN_RANGE = sql.SQL("t.{id} BETWEEN %s AND %s")
_THROUGH = sql.SQL(
    "EXISTS (SELECT FROM {parent_schema}.{parent} AS p"
    " WHERE p.{column} = t.{key} AND p.{id} BETWEEN %s AND %s)"
)
_NOT_OWN = sql.SQL(" AND (r.{own} BETWEEN %s AND %s) IS NOT TRUE")
_NOT_OWN_THROUGH = sql.SQL(
    " AND NOT EXISTS (SELECT FROM {parent_schema}.{parent} AS q"
    " WHERE q.{column} = r.{key} AND q.{own} BETWEEN %s AND %s)"
)
_RESTART = sql.SQL("ALTER TABLE {schema}.{table} {restarts}")
_EXISTING = sql.SQL("SELECT {columns} FROM {schema}.{table}{where} ORDER BY {pk}")
_NAMED_BY = sql.SQL(" WHERE {by} = ANY(%s)")
_LEARN = sql.SQL("SELECT {pk}, {id} FROM {schema}.{table} WHERE {id} = ANY(%s)")
_SET_PATHS = sql.SQL(
    "UPDATE {schema}.{table} AS t SET {path} = v.path::ltree"
    " FROM unnest(%s::uuid[], %s::text[]) AS v(id, path) WHERE t.{id} = v.id"
)


@contextmanager
def transaction(database_url: str, *, commit: bool = True) -> Iterator[Connection]:
    """A connection whose one transaction is committed when the block ends, unless *commit*
    is false, and rolled back if it raises."""
    try:
        connection = psycopg.connect(checked_url(database_url))
    except psycopg.OperationalError as error:
        raise _unreachable(database_url, error) from error
    with connection:
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        if commit:
            connection.commit()
        else:
            connection.rollback()


@contextmanager
def exclusive(database_url: str, scenario_id: int) -> Iterator[None]:
    """One apply of *scenario_id* at a time: a lock on a connection of its own, held until
    the block ends, committed or not.

    Taken before the already-applied check, it makes a second apply of the scenario wait
    for the first's transaction: once the first commits, the second's check sees its
    rows; once it rolls back, the second proceeds.
    """
    with transaction(database_url) as connection:
        connection.execute(_LOCK, [SEMIS_LOCK_CLASS, scenario_id])
        yield


def checked_url(database_url: str) -> str:
    """*database_url*, refused unless libpq reads it as written: checked once, before
    confiture or psycopg is given it, since both repeat what they cannot read, password
    included.

    A password holding an unencoded ``@`` or ``/`` is read by libpq as part of the host,
    or as a host and a port: such a URL is refused as malformed too.
    """
    try:
        parts = conninfo_to_dict(database_url)
    except psycopg.ProgrammingError:
        parts = None
    if parts is None or "@" in str(parts.get("host", "")) or not _ports(str(parts.get("port", ""))):
        raise UnreachableDatabaseError(
            "cannot connect to PostgreSQL: the database URL is malformed",
            resolution_hint=(
                "Check the URL's form, postgresql://user:password@host:port/database, and "
                "percent-encode any reserved character in its password."
            ),
        ) from None
    return database_url


def _ports(given: str) -> bool:
    """Whether *given*, libpq's ``port``, is one or more numbers, or none."""
    return all(port.isdigit() for port in given.split(",") if port)


def _unreachable(database_url: str, error: psycopg.OperationalError) -> UnreachableDatabaseError:
    """*error*, naming where semis tried to connect and never the URL's password."""
    parts = conninfo_to_dict(database_url)
    where = f"{parts.get('host', 'localhost')}:{parts.get('port', 5432)}/{parts.get('dbname', '')}"
    return UnreachableDatabaseError(
        f"cannot connect to PostgreSQL at {where}: {str(error).splitlines()[0]}",
        resolution_hint=(
            "Check that PostgreSQL is running there, and that the URL names a user, a "
            "password and a database it accepts."
        ),
    )


def holds(
    connection: Connection, table: ObjectRef, natural_id: str, bounds: tuple[object, object]
) -> bool:
    """Whether *table* holds a row whose *natural_id* lies within *bounds*, as
    *connection* sees it: one query, on the natural id's index, read-only."""
    query = _HOLDS.format(
        schema=sql.Identifier(table.schema),
        table=sql.Identifier(table.name),
        id=sql.Identifier(natural_id),
    )
    with _built(table):
        row = connection.execute(query, list(bounds)).fetchone()
    return bool(row and row[0])


class Through(NamedTuple):
    """A table whose scenario rows are found by their parent's: its *key* holds
    *parent*'s *column*."""

    key: str
    parent: ObjectRef
    column: str


class Scope(NamedTuple):
    """Where a scenario's rows of a table lie: *column*'s value within *bounds* — the
    table's own column, or, *through* its parent, the parent's."""

    column: str
    bounds: tuple[object, object]
    through: Through | None = None


def delete(connection: Connection, table: ObjectRef, scope: Scope) -> int:
    """Delete *table*'s rows *scope* finds, in *connection*'s transaction: how many."""
    if scope.through is None:
        return delete_range(connection, table, scope.column, scope.bounds)
    query = _DELETE_THROUGH.format(
        schema=sql.Identifier(table.schema),
        table=sql.Identifier(table.name),
        parent_schema=sql.Identifier(scope.through.parent.schema),
        parent=sql.Identifier(scope.through.parent.name),
        key=sql.Identifier(scope.through.key),
        column=sql.Identifier(scope.through.column),
        id=sql.Identifier(scope.column),
    )
    with _built(table):
        return connection.execute(query, list(scope.bounds)).rowcount


def delete_range(
    connection: Connection, table: ObjectRef, natural_id: str, bounds: tuple[object, object]
) -> int:
    """Delete *table*'s rows whose *natural_id* lies within *bounds*, in *connection*'s
    transaction: how many there were."""
    query = _DELETE.format(
        schema=sql.Identifier(table.schema),
        table=sql.Identifier(table.name),
        id=sql.Identifier(natural_id),
    )
    with _built(table):
        return connection.execute(query, list(bounds)).rowcount


def lock_shared(connection: Connection, tables: Sequence[ObjectRef]) -> None:
    """Lock *tables* ``SHARE`` until *connection*'s transaction ends: their rows may be
    read, and none written, so none starts pointing at a row a reset deletes."""
    if tables:
        names = sql.SQL(", ").join(sql.Identifier(table.schema, table.name) for table in tables)
        connection.execute(_LOCK_SHARED.format(tables=names))


def pointing(
    connection: Connection,
    reference: Reference,
    *,
    scope: Scope,
    own: Scope | None,
) -> int:
    """How many rows of *reference*'s table point, by it, at a row of its target *scope*
    finds: every such row, or, when *own* scopes the referencing table's rows, those
    outside it."""
    if scope.through is None:
        scoped = _IN_RANGE.format(id=sql.Identifier(scope.column))
    else:
        scoped = _THROUGH.format(
            parent_schema=sql.Identifier(scope.through.parent.schema),
            parent=sql.Identifier(scope.through.parent.name),
            column=sql.Identifier(scope.through.column),
            key=sql.Identifier(scope.through.key),
            id=sql.Identifier(scope.column),
        )
    query = _POINTING.format(
        schema=sql.Identifier(reference.table.schema),
        table=sql.Identifier(reference.table.name),
        target_schema=sql.Identifier(reference.target.schema),
        target=sql.Identifier(reference.target.name),
        joined=sql.SQL(" AND ").join(
            sql.SQL("t.{} = r.{}").format(sql.Identifier(to), sql.Identifier(by))
            for by, to in zip(reference.columns, reference.target_columns, strict=True)
        ),
        scoped=scoped,
    )
    params = list(scope.bounds)
    if own is not None and own.through is None:
        query += _NOT_OWN.format(own=sql.Identifier(own.column))
    elif own is not None and own.through is not None:
        query += _NOT_OWN_THROUGH.format(
            parent_schema=sql.Identifier(own.through.parent.schema),
            parent=sql.Identifier(own.through.parent.name),
            column=sql.Identifier(own.through.column),
            key=sql.Identifier(own.through.key),
            own=sql.Identifier(own.column),
        )
    params += [] if own is None else list(own.bounds)
    (rows,) = connection.execute(query, params).fetchone()
    return rows


def restart(connection: Connection, table: ObjectRef, identities: Sequence[str]) -> None:
    """Restart each of *table*'s *identities*, in *connection*'s transaction: the keys
    its next rows take begin again, as ``TRUNCATE … RESTART IDENTITY`` makes them."""
    restarts = sql.SQL(", ").join(
        sql.SQL("ALTER COLUMN {} RESTART").format(sql.Identifier(column)) for column in identities
    )
    connection.execute(
        _RESTART.format(
            schema=sql.Identifier(table.schema),
            table=sql.Identifier(table.name),
            restarts=restarts,
        )
    )


def count(connection: Connection, table: ObjectRef) -> int:
    """How many rows *table* holds, as *connection* sees it."""
    query = _COUNT.format(schema=sql.Identifier(table.schema), table=sql.Identifier(table.name))
    (rows,) = connection.execute(query).fetchone()
    return rows


def existing_keys(
    connection: Connection, table: TableKeys, *, by: str, values: Sequence[str] | None
) -> list[int]:
    """The ``pk_*`` of rows *table* already holds, for a run that does not write them:
    every row's, ordered by key, or those whose *by* column is one of *values*, in their
    order. The refusals are ``existing_rows``'."""
    named = by if values is not None else None
    rows = existing_rows(connection, table, by=named, values=values, natural_id=None)
    return [pk for pk, _, _ in rows]


def existing_rows(
    connection: Connection,
    table: TableKeys,
    *,
    by: str | None,
    values: Sequence[str] | None,
    natural_id: str | None,
) -> list[tuple[int, object, object]]:
    """The rows *table* already holds, for a run that does not write them: each one's
    ``pk_*``, then its *by* value and its *natural_id*, each ``None`` unless asked for.

    Every row, ordered by key, or those whose *by* column is one of *values*, in their
    order; only the columns asked for are read, so a column the table lacks is never
    named. Refused: a table holding no row, a value no row matches, a value two rows
    share.
    """
    if table.surrogate_pk is None:
        raise ResolutionError(
            f"existing {table.ref.display} shows no surrogate key to point at",
            resolution_hint="Read-back points a foreign key at a pk_* column.",
        )
    if values is not None and by is None:
        raise ValueError("rows named by their values need the column that names them")
    asked = [table.surrogate_pk, *(column for column in (by, natural_id) if column is not None)]
    query = _EXISTING.format(
        columns=sql.SQL(", ").join(sql.Identifier(column) for column in asked),
        schema=sql.Identifier(table.ref.schema),
        table=sql.Identifier(table.ref.name),
        where=sql.SQL("")
        if by is None or values is None
        else _NAMED_BY.format(by=sql.Identifier(by)),
        pk=sql.Identifier(table.surrogate_pk),
    )
    with _built(table.ref):
        fetched = connection.execute(query, None if values is None else [list(values)]).fetchall()
    rows = [_padded(row, by=by, natural_id=natural_id) for row in fetched]
    if values is None:
        if not rows:
            raise ResolutionError(
                f"existing {table.ref.display} holds no rows to point at",
                resolution_hint="Apply the rows it holds first, or generate it in the run.",
            )
        return rows
    return _in_order(table, rows, by=cast("str", by), values=values)


def _padded(
    row: Sequence[object], *, by: str | None, natural_id: str | None
) -> tuple[int, object, object]:
    """*row*, the columns ``existing_rows`` read, as ``(pk, by value, natural id)``."""
    found = iter(row[1:])
    named = next(found) if by is not None else None
    uuid = next(found) if natural_id is not None else None
    return cast("int", row[0]), named, uuid


def _in_order(
    table: TableKeys,
    rows: list[tuple[int, object, object]],
    *,
    by: str,
    values: Sequence[str],
) -> list[tuple[int, object, object]]:
    """*rows* in the order of *values*, refusing a value no row has, or two rows share."""
    found: dict[object, list[tuple[int, object, object]]] = {}
    for row in rows:
        found.setdefault(row[1], []).append(row)
    for value in values:
        matched = len(found.get(value, []))
        if matched == 0:
            raise ResolutionError(
                f"existing {table.ref.display} has no row whose {by} is {value!r}",
                resolution_hint=f"Name, under where:, rows the table holds by their {by}.",
            )
        if matched > 1:
            raise ResolutionError(
                f"existing {table.ref.display} has {matched} rows whose {by} is {value!r}",
                resolution_hint=(
                    f"Name, under where:, rows the table holds by their {by}, one row each: "
                    "which of two a child points at would be arbitrary."
                ),
            )
    return [found[value][0] for value in values]


@contextmanager
def _built(table: ObjectRef) -> Iterator[None]:
    """Refuse a read of *table* in a database whose schema is not built: the first
    query a run makes, before anything is written."""
    try:
        yield
    except psycopg.errors.UndefinedTable as error:
        raise SchemaNotBuiltError(
            f"the database holds no {table.display}",
            resolution_hint="Build the schema in this database, then apply the scenario.",
        ) from error


def learn(connection: Connection, table: TableFacts, uuids: Sequence[object]) -> dict[object, int]:
    """The ``pk_*`` PostgreSQL gave each of *uuids*, by natural id, as *connection* sees them.

    Read on the caller's connection, so rows applied in its open transaction are found.
    A UUID with no row is absent from the result.
    """
    if table.surrogate_pk is None or table.natural_id is None:
        raise ResolutionError(
            f"{table.ref.display} shows no surrogate key and natural id to read back",
            resolution_hint="Read-back needs a pk_* and a UUID id; use prep-seed mode otherwise.",
        )
    query = _LEARN.format(
        pk=sql.Identifier(table.surrogate_pk),
        id=sql.Identifier(table.natural_id),
        schema=sql.Identifier(table.ref.schema),
        table=sql.Identifier(table.ref.name),
    )
    return {uuid: pk for pk, uuid in connection.execute(query, [list(uuids)]).fetchall()}


def set_paths(
    connection: Connection, table: TableFacts, column: str, paths: Mapping[object, str]
) -> None:
    """Set *table*'s ``ltree`` *column* to *paths*, each keyed by its row's natural id.

    One statement for the whole mapping, on the caller's connection and in its
    transaction. A hierarchy's paths are set once its level's keys are learned.
    """
    if table.natural_id is None:
        raise ResolutionError(
            f"{table.ref.display} shows no natural id to set its paths by",
            resolution_hint="Read-back needs a UUID id; use prep-seed mode otherwise.",
        )
    query = _SET_PATHS.format(
        schema=sql.Identifier(table.ref.schema),
        table=sql.Identifier(table.ref.name),
        path=sql.Identifier(column),
        id=sql.Identifier(table.natural_id),
    )
    connection.execute(query, [list(paths), list(paths.values())])
