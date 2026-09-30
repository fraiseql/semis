"""Learning a parent's ``pk_*`` after its seed is applied (read-back mode, ARCHITECTURE §5).

The one module that imports ``psycopg``, and the only SQL semis writes (D9). That SQL is
two shapes — learning keys, and setting a hierarchy's paths from them; their identifiers
come from the model, composed with ``psycopg.sql.Identifier``, and their values are
parameters.
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from fraiseql_semis.errors import ResolutionError, UnreachableDatabaseError
from fraiseql_semis.schema import Connection, TableFacts

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
        connection = psycopg.connect(database_url)
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
