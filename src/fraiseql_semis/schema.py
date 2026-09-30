"""The schema, as semis reads it: confiture's model plus the table-code registry.

With ``seeds.py``, one of the two modules that call ``confiture.platform`` (D1). The
confiture types semis passes around are re-exported here, so the rest of the package
imports them from ``fraiseql_semis.schema`` and a rename in confiture lands in one file.
"""

from collections.abc import Iterable, Sequence
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
    diff,
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
    "NotInModelError",
    "ObjectRef",
    "SchemaFacts",
    "SchemaModel",
    "SchemaSource",
    "SeedError",
    "SeedFile",
    "SourceKind",
    "TableFacts",
    "database_url",
]

CONFITURE_VERSION = version("fraiseql-confiture")
"""The confiture that reads every schema here, recorded in a pin beside its digest."""

SourceKind = Literal["ddl", "live"]
"""How a schema was read: from DDL (``parse_schema``) or a live database (``introspect``).

The two spell the same schema differently, so facts from one are comparable only with
facts from the same kind (ARCHITECTURE §8)."""


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
        source: SchemaSource | None = None,
    ) -> None:
        self._model = model
        self._codes = table_codes
        self._source_kind: SourceKind = source_kind
        self._source = source
        self._refs = {ref.display: ref for ref in model.tables}

    @classmethod
    def from_source(cls, source: SchemaSource, *, table_codes: TableCodes) -> "SchemaFacts":
        """From DDL text, a path, or a sequence of them — no project, no database."""
        return cls(parse_schema(source), table_codes, source_kind="ddl", source=source)

    @classmethod
    def from_env(
        cls, env: str, *, project_dir: Path | None = None, table_codes: TableCodes
    ) -> "SchemaFacts":
        """From a confiture project's build for *env*."""
        return cls(parse_schema(env=env, project_dir=project_dir), table_codes, source_kind="ddl")

    @classmethod
    def from_database(
        cls, database: str | Connection, *, schemas: Sequence[str], table_codes: TableCodes
    ) -> "SchemaFacts":
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

    def snapshot(self) -> str | None:
        """The DDL text these facts were read from; ``None`` unless built ``from_source``.

        A path is read as ``parse_schema`` reads it: a directory is every ``.sql`` under
        it, sorted by path, and a sequence is its paths in order. confiture publishes no
        call returning an env build's text, and a live database has none.
        """
        if self._source is None:
            return None
        if isinstance(self._source, str):
            return self._source
        paths = [self._source] if isinstance(self._source, Path) else self._source
        return "\n".join(_read(Path(path)) for path in paths)

    def changes_since(self, snapshot: str) -> tuple[str, ...] | None:
        """What ``diff`` reports from *snapshot* to this schema; ``None`` with no source to diff."""
        if self._source is None:
            return None
        return tuple(str(change) for change in diff(snapshot, self._source).changes)

    def facts_for(self, table: str) -> TableFacts:
        """*table*'s writable columns with their facts, its trinity roles and its code.

        *table* is schema-qualified. A table the model does not hold raises confiture's
        ``NotInModelError``; one without a code raises ``CodeRegistryError``.
        """
        hints = naming_hints(self._model, table)
        table_code = self._codes.code_for(table)
        return TableFacts(
            ref=self._refs[table],
            table_code=table_code,
            surrogate_pk=hints.surrogate_pk,
            natural_id=hints.natural_id,
            columns=tuple(
                column_facts(self._model, table, column.name)
                for column in writable_columns(self._model, table)
            ),
        )

    def columns(self, table: str) -> tuple[ColumnFacts, ...] | None:
        """*table*'s writable columns with their facts; ``None`` when the model lacks it.

        No table code is needed: a staging twin holds no UUIDs of its own.
        """
        if table not in self._refs:
            return None
        return tuple(
            column_facts(self._model, table, column.name)
            for column in writable_columns(self._model, table)
        )

    def insert_order(self, tables: Iterable[str] | None = None) -> list[ObjectRef]:
        """Parents before children, from the real foreign keys."""
        return dependency_order(self._model, tables=tables)


def _read(path: Path) -> str:
    files = sorted(path.rglob("*.sql")) if path.is_dir() else [path]
    return "\n".join(file.read_text(encoding="utf-8") for file in files)


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
