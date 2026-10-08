"""The schema, as semis reads it: confiture's model plus the table-code registry.

With ``seeds.py``, one of the two modules that call ``confiture.platform`` (D1). The
confiture types semis passes around are re-exported here, so the rest of the package
imports them from ``fraiseql_semis.schema`` and a rename in confiture lands in one file.
"""

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from confiture.cli.dsn import resolve_database_url
from confiture.config.environment import Environment
from confiture.platform import (
    ColumnFacts,
    ConfigurationError,
    ConfiturError,
    Connection,
    DependencyCycleError,
    NotInModelError,
    ObjectRef,
    SchemaModel,
    SchemaSource,
    SeedError,
    SeedFile,
    column_facts,
    dependency_order,
    introspect,
    naming_hints,
    parse_schema,
    writable_columns,
)

from fraiseql_semis.codes import TableCodes

__all__ = [
    "CONFITURE_VERSION",
    "ColumnFacts",
    "ConfigurationError",
    "ConfiturError",
    "Connection",
    "DependencyCycleError",
    "LiveSchema",
    "NotInModelError",
    "ObjectRef",
    "Reference",
    "SchemaFacts",
    "SchemaModel",
    "SchemaSource",
    "SeedError",
    "SeedFile",
    "SourceKind",
    "TableFacts",
    "TableKeys",
    "database_url",
]

CONFITURE_VERSION = version("fraiseql-confiture")
"""The confiture that reads every schema here, recorded in a pin beside its digest."""

SourceKind = Literal["ddl", "live"]
"""How a schema was read: from DDL (``parse_schema``) or a live database (``introspect``).

The two spell the same schema differently, so facts from one are comparable only with
facts from the same kind (ARCHITECTURE §8)."""


@dataclass(frozen=True)
class TableKeys:
    """How a table's rows are told apart: its reference, surrogate key and natural id.

    A table semis reads and never writes has these and no code."""

    ref: ObjectRef
    surrogate_pk: str | None
    natural_id: str | None


@dataclass(frozen=True)
class TableFacts:
    """What semis needs to generate rows for one table."""

    ref: ObjectRef
    table_code: int
    surrogate_pk: str | None
    natural_id: str | None
    columns: tuple[ColumnFacts, ...]


class SchemaFacts:
    """Confiture's model of a schema, with the table codes semis encodes UUIDs from."""

    def __init__(
        self,
        model: SchemaModel,
        table_codes: TableCodes,
        *,
        source_kind: SourceKind,
    ) -> None:
        self._model = model
        self._codes = table_codes
        self._source_kind: SourceKind = source_kind
        self._refs = {ref.display: ref for ref in model.tables}
        # Each table's facts, read from the model once: a run asks for them many times.
        self._tables: dict[str, TableFacts] = {}
        self._columns: dict[str, tuple[ColumnFacts, ...]] = {}

    @classmethod
    def from_source(cls, source: SchemaSource, *, table_codes: TableCodes) -> SchemaFacts:
        """From DDL text, a path, or a sequence of them — no project, no database."""
        return cls(parse_schema(source), table_codes, source_kind="ddl")

    @classmethod
    def from_env(
        cls, env: str, *, project_dir: Path | None = None, table_codes: TableCodes
    ) -> SchemaFacts:
        """From a confiture project's build for *env*."""
        return cls(parse_schema(env=env, project_dir=project_dir), table_codes, source_kind="ddl")

    @classmethod
    def from_database(
        cls, database: str | Connection, *, schemas: Sequence[str], table_codes: TableCodes
    ) -> SchemaFacts:
        """From a live database, reading only *schemas*."""
        return cls(introspect(database, schemas=schemas), table_codes, source_kind="live")

    @property
    def source_kind(self) -> SourceKind:
        """``ddl`` or ``live``: which of confiture's two readings produced these facts."""
        return self._source_kind

    @property
    def model(self) -> SchemaModel:
        """Confiture's model, for the calls that take one."""
        return self._model

    def ref(self, table: str) -> ObjectRef | None:
        """*table*'s reference in the model; ``None`` when the model lacks it."""
        return self._refs.get(table)

    def keys_for(self, table: str) -> TableKeys:
        """*table*'s keys, its trinity roles; no code is needed. A table the model does
        not hold raises confiture's ``NotInModelError``."""
        hints = naming_hints(self._model, table)
        return TableKeys(self._refs[table], hints.surrogate_pk, hints.natural_id)

    def facts_for(self, table: str) -> TableFacts:
        """*table*'s writable columns with their facts, its trinity roles and its code.

        *table* is schema-qualified. A table the model does not hold raises confiture's
        ``NotInModelError``; one without a code raises ``CodeRegistryError``.
        """
        found = self._tables.get(table)
        if found is None:
            hints = naming_hints(self._model, table)
            table_code = self._codes.code_for(table)
            found = self._tables[table] = TableFacts(
                ref=self._refs[table],
                table_code=table_code,
                surrogate_pk=hints.surrogate_pk,
                natural_id=hints.natural_id,
                columns=self._writable(table),
            )
        return found

    def columns(self, table: str) -> tuple[ColumnFacts, ...] | None:
        """*table*'s writable columns with their facts; ``None`` when the model lacks it.

        No table code is needed: a staging twin holds no UUIDs of its own.
        """
        if table not in self._refs:
            return None
        return self._writable(table)

    def _writable(self, table: str) -> tuple[ColumnFacts, ...]:
        found = self._columns.get(table)
        if found is None:
            found = self._columns[table] = tuple(
                column_facts(self._model, table, column.name)
                for column in writable_columns(self._model, table)
            )
        return found

    def insert_order(self, tables: Iterable[str] | None = None) -> list[ObjectRef]:
        """Parents before children, from the real foreign keys."""
        return dependency_order(self._model, tables=tables)


@dataclass(frozen=True)
class Reference:
    """A foreign key, *constraint*: *table*'s *columns* pointing at *target*'s
    *target_columns*."""

    table: ObjectRef
    constraint: str
    columns: tuple[str, ...]
    target: ObjectRef
    target_columns: tuple[str, ...]


@dataclass(frozen=True)
class LiveSchema:
    """What a reset reads of the database itself: confiture's model of every schema
    in it, read once, in the caller's transaction."""

    model: SchemaModel

    @classmethod
    def read(cls, connection: Connection) -> LiveSchema:
        return cls(introspect(connection))

    def references_into(self, tables: Collection[ObjectRef]) -> list[Reference]:
        """Every foreign key into one of *tables*, from a table in any schema: a key from
        a table semis never reads is seen too. In the order of the referencing tables'
        names. A live model names the referenced columns even where the DDL named none
        (``tests/contract/test_introspect_names_every_referencing_key.py``)."""
        wanted: dict[tuple[str, str], ObjectRef] = {(t.schema, t.name): t for t in tables}
        references: list[Reference] = []
        for ref, table in sorted(self.model.tables.items(), key=lambda item: item[0].display):
            for constraint in table.constraints:
                target = constraint.ref_table
                into = None if target is None else wanted.get((target.schema or "", target.name))
                if into is None:
                    continue
                references.append(
                    Reference(
                        ref, constraint.name, constraint.columns, into, constraint.ref_columns
                    )
                )
        return references

    def identities(self, table: ObjectRef) -> tuple[str, ...]:
        """*table*'s identity columns, whose sequences a reset restarts once it is empty."""
        found = next(
            (found for ref, found in self.model.tables.items() if ref.display == table.display),
            None,
        )
        columns = found.columns if found is not None else ()
        return tuple(column.name for column in columns if column.identity is not None)


def database_url(
    flag: str | None, *, env: str | None, project_dir: Path | None, mutating: bool
) -> str | None:
    """The database a command connects to, by confiture's own precedence (#152).

    *flag* is ``--database-url``; *env* is the confiture environment the project names,
    which counts as an explicit config. A *mutating* command refuses a URL found only in
    the ambient ``DATABASE_URL``. ``None`` when no source names one.

    The precedence is ``confiture.cli.dsn``'s, which ``confiture.platform`` does not
    publish; ``tests/contract/test_database_url_precedence.py`` pins what semis relies on.
    """
    config = None if env is None else _environment_file(env, project_dir)
    found = resolve_database_url(
        flag,
        config,
        config_explicit=env is not None,
        require_intentional_source=mutating,
    )
    if found is None and env is not None:
        return Environment.load(env, project_dir).database_url
    return found


def _environment_file(env: str, project_dir: Path | None) -> Path:
    return (project_dir or Path.cwd()) / "db" / "environments" / f"{env}.yaml"
