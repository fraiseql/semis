"""Learning a parent's ``pk_*`` after its seed is applied (read-back mode, ARCHITECTURE §5).

The one module that imports ``psycopg``, and the only SQL semis writes (D9): taking a
scenario's lock, asking whether its rows are already applied, deleting them for a reset,
reading the keys of rows a run did not write, learning keys, and setting a hierarchy's
paths from them. Their identifiers come from the model, composed with
``psycopg.sql.Identifier``, and their values are parameters.
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager

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
_COUNT = sql.SQL("SELECT count(*) FROM {schema}.{table}")
_LOCK_SHARED = sql.SQL("LOCK TABLE {tables} IN SHARE MODE")
_POINTING = sql.SQL(
    "SELECT count(*) FROM {schema}.{table} AS r WHERE EXISTS (SELECT FROM {target_schema}."
    "{target} AS t WHERE {joined} AND t.{id} BETWEEN %s AND %s)"
)
_NOT_OWN = sql.SQL(" AND (r.{own} BETWEEN %s AND %s) IS NOT TRUE")
_RESTART = sql.SQL("ALTER TABLE {schema}.{table} {restarts}")
_EVERY_KEY = sql.SQL("SELECT {pk} FROM {schema}.{table} ORDER BY {pk}")
_KEYS_BY = sql.SQL("SELECT {by}, {pk} FROM {schema}.{table} WHERE {by} = ANY(%s)")
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
    natural_id: str,
    bounds: tuple[object, object],
    own: tuple[str, tuple[object, object]] | None,
) -> int:
    """How many rows of *reference*'s table point, by it, at a row of its target whose
    *natural_id* lies within *bounds*: every such row, or, when *own* gives the
    referencing table's natural id and range, those outside that range."""
    query = _POINTING.format(
        schema=sql.Identifier(reference.table.schema),
        table=sql.Identifier(reference.table.name),
        target_schema=sql.Identifier(reference.target.schema),
        target=sql.Identifier(reference.target.name),
        joined=sql.SQL(" AND ").join(
            sql.SQL("t.{} = r.{}").format(sql.Identifier(to), sql.Identifier(by))
            for by, to in zip(reference.columns, reference.target_columns, strict=True)
        ),
        id=sql.Identifier(natural_id),
    )
    params = list(bounds)
    if own is not None:
        query += _NOT_OWN.format(own=sql.Identifier(own[0]))
        params += list(own[1])
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
    order. A table holding none of them, or a value matching no row, is refused."""
    if table.surrogate_pk is None:
        raise ResolutionError(
            f"existing {table.ref.display} shows no surrogate key to point at",
            resolution_hint="Read-back points a foreign key at a pk_* column.",
        )
    parts = {
        "pk": sql.Identifier(table.surrogate_pk),
        "by": sql.Identifier(by),
        "schema": sql.Identifier(table.ref.schema),
        "table": sql.Identifier(table.ref.name),
    }
    if values is None:
        with _built(table.ref):
            keys = [pk for (pk,) in connection.execute(_EVERY_KEY.format(**parts)).fetchall()]
        if not keys:
            raise ResolutionError(
                f"existing {table.ref.display} holds no rows to point at",
                resolution_hint="Apply the rows it holds first, or generate it in the run.",
            )
        return keys
    with _built(table.ref):
        found = dict(connection.execute(_KEYS_BY.format(**parts), [list(values)]).fetchall())
    missing = next((value for value in values if value not in found), None)
    if missing is not None:
        raise ResolutionError(
            f"existing {table.ref.display} has no row whose {by} is {missing!r}",
            resolution_hint=f"Name, under where:, rows the table holds by their {by}.",
        )
    return [found[value] for value in values]


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
