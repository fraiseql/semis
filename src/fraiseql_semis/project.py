"""A project: the schema its scenarios are written against, its table codes, its scenarios.

``semis.yaml`` loads into a :class:`Project`, and a tool that drives semis from Python
(specql, fraiseql) builds one directly, with no file. A scenario names neither codes nor
a schema, so a command reads them here.
"""

import importlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ProjectError, refuse_unknown
from fraiseql_semis.faker_provider import Library, Provider
from fraiseql_semis.providers import SHIPPED
from fraiseql_semis.readback import checked_url
from fraiseql_semis.scenario import ScenarioManager, read_yaml
from fraiseql_semis.schema import SchemaFacts, database_url
from fraiseql_semis.staging import Staging

DEFAULT_CONFIG = Path("semis.yaml")
_PROJECT_KEYS = {"schema", "table_codes", "scenarios", "providers", "prep_seed"}
_PREP_SEED_KEYS = {"prep_seed_schema", "schema_dir", "catalog_schema"}
_SCHEMA_KEYS = {"ddl", "env", "project_dir", "database"}
_SOURCES = ("ddl", "env", "database")
_PROJECT_YAML = (
    "Fix the YAML at that line: semis.yaml maps the keys the semis.yaml reference lists."
)
ENTRY_POINTS = "fraiseql_semis.providers"
"""The entry-point group an installed provider library registers under, by its name."""


@dataclass(frozen=True)
class ProjectSchema:
    """Where a project's schema is read from: DDL, a confiture environment, or a database.

    Exactly one of *ddl*, *env* and *schemas* is given. *project_dir* is the confiture
    project an *env* is built in; *schemas* are the database schemas introspected.
    """

    ddl: Path | None = None
    env: str | None = None
    project_dir: Path | None = None
    schemas: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        given = [value for value in (self.ddl, self.env, self.schemas) if value is not None]
        if len(given) != 1:
            raise ProjectError("a project's schema is read from exactly one of ddl, env, schemas")

    def read(self, table_codes: TableCodes, *, database_url: str | None = None) -> SchemaFacts:
        """These facts, read from the source this names."""
        if self.ddl is not None:
            return SchemaFacts.from_source(self.ddl, table_codes=table_codes)
        if self.env is not None:
            return SchemaFacts.from_env(
                self.env, project_dir=self.project_dir, table_codes=table_codes
            )
        if database_url is None:
            raise ProjectError(
                "this project reads its schema from a database, and no database URL was given",
                resolution_hint="Pass --database-url, or set CONFITURE_DATABASE_URL.",
            )
        return SchemaFacts.from_database(
            database_url, schemas=self.schemas or (), table_codes=table_codes
        )


@dataclass(frozen=True)
class Project:
    """The table codes, the schema and the scenarios directory the commands work with.

    *schema* is where the schema is read from, or the facts themselves for a caller that
    already holds them. *libraries* are the provider libraries the project enables, and
    *providers* its own providers by the names its scenarios give them. *staging* is
    where its prep-seed scenarios write. *schema_dir* is the schema tree holding the
    resolvers that confiture's prep-seed levels read, and *catalog_schema* the final
    tables' fallback schema, passed only when named.
    """

    table_codes: TableCodes
    schema: ProjectSchema | SchemaFacts
    scenarios: Path
    libraries: tuple[Library, ...] = ()
    providers: Mapping[str, Provider] = field(default_factory=dict)
    staging: Staging = field(default_factory=Staging)
    schema_dir: Path | None = None
    catalog_schema: str | None = None

    @classmethod
    def find(cls, config: Path | None) -> Project | None:
        """The project *config* names, or ``./semis.yaml`` when it exists; else ``None``."""
        if config is None:
            if not DEFAULT_CONFIG.is_file():
                return None
            config = DEFAULT_CONFIG
        return cls.load(config)

    @classmethod
    def load(cls, path: Path | str) -> Project:
        """The project the file at *path* describes; its paths are relative to that file.

        A malformed file is refused with ``ProjectError`` naming the key at fault.
        """
        path = Path(path)
        if not path.is_file():
            raise ProjectError(
                f"no project file at {path}",
                resolution_hint="Write a semis.yaml naming the schema and the table codes.",
            )
        data = read_yaml(path, ProjectError, _PROJECT_YAML)
        if not isinstance(data, dict):
            raise ProjectError(f"{path} holds no mapping")
        refuse_unknown(str(path), data, _PROJECT_KEYS, error=ProjectError)
        for key in ("schema", "table_codes"):
            if key not in data:
                raise ProjectError(f"{path} has no {key}:")
        root = path.parent
        scenarios = data.get("scenarios", "scenarios")
        if not isinstance(scenarios, str):
            raise ProjectError(f"{path}: scenarios: is a string, the scenarios directory")
        libraries, providers = _providers(path, data.get("providers", []))
        staging, schema_dir, catalog_schema = _prep_seed(path, data.get("prep_seed", {}))
        return cls(
            table_codes=_table_codes(path, data["table_codes"]),
            schema=_schema(path, data["schema"]),
            scenarios=root / scenarios,
            libraries=libraries,
            providers=providers,
            staging=staging,
            schema_dir=schema_dir,
            catalog_schema=catalog_schema,
        )

    def database_url(self, flag: str | None = None, *, mutating: bool) -> str:
        """The database a command connects to: *flag*, else confiture's precedence.

        A project reading an ``env:`` finds its URL in that environment's file. A
        *mutating* command refuses an ambient ``DATABASE_URL`` alone, as confiture does.
        """
        found = self.find_database_url(flag, mutating=mutating)
        if found is None:
            raise ProjectError(
                "no database URL: this command connects to a database",
                resolution_hint="Pass --database-url, or set CONFITURE_DATABASE_URL.",
            )
        return found

    def find_database_url(self, flag: str | None = None, *, mutating: bool) -> str | None:
        """As ``database_url``, but ``None`` when no source names one."""
        env = project_dir = None
        if isinstance(self.schema, ProjectSchema) and self.schema.env is not None:
            env, project_dir = self.schema.env, self.schema.project_dir
        return database_url(flag, env=env, project_dir=project_dir, mutating=mutating)

    def resolvers(self) -> Path:
        """The schema tree confiture's prep-seed levels read the resolvers from.

        ``prep_seed: schema_dir:``, else the ``ddl:`` source when it is a directory.
        """
        if self.schema_dir is not None:
            return self.schema_dir
        schema = self.schema
        if isinstance(schema, ProjectSchema) and schema.ddl is not None and schema.ddl.is_dir():
            return schema.ddl
        raise ProjectError(
            "no schema directory to read the resolvers from",
            resolution_hint="Name it in semis.yaml: prep_seed: schema_dir: db/0_schema",
        )

    def facts(self, *, database_url: str | None = None) -> SchemaFacts:
        """The schema's facts, read now from the source the project names.

        A ``database:`` schema is read at *database_url*, or where confiture's
        precedence points: reading it is not a mutation.
        """
        if isinstance(self.schema, SchemaFacts):
            return self.schema
        if self.schema.schemas is not None:
            database_url = checked_url(self.database_url(database_url, mutating=False))
        return self.schema.read(self.table_codes, database_url=database_url)

    def manager(self, *, database_url: str | None = None) -> ScenarioManager:
        """A scenario manager over this schema, with the providers the project names."""
        return ScenarioManager(
            self.facts(database_url=database_url),
            providers=self.providers,
            libraries=self.libraries,
            staging=self.staging,
        )


def _schema(path: Path, block: object) -> ProjectSchema:
    """The ``schema:`` block: exactly one source, its paths resolved beside the file."""
    if not isinstance(block, dict):
        raise ProjectError(f"{path}: schema: is a mapping naming ddl:, env: or database:")
    refuse_unknown(f"{path}: schema:", block, _SCHEMA_KEYS, error=ProjectError)
    given = [key for key in _SOURCES if key in block]
    if len(given) != 1:
        raise ProjectError(f"{path}: schema: names exactly one of ddl:, env: and database:")
    if "project_dir" in block and "env" not in block:
        raise ProjectError(f"{path}: schema: project_dir: goes with env:")
    root = path.parent
    if "database" in block:
        database = block["database"]
        schemas = database.get("schemas") if isinstance(database, dict) else None
        if not isinstance(schemas, list) or not all(isinstance(s, str) for s in schemas):
            raise ProjectError(f"{path}: schema: database: names its schemas: as a list")
        return ProjectSchema(schemas=tuple(schemas))
    for key in ("ddl", "env", "project_dir"):
        if key in block and not isinstance(block[key], str):
            raise ProjectError(f"{path}: schema: {key}: is a string")
    if "ddl" in block:
        return ProjectSchema(ddl=root / block["ddl"])
    return ProjectSchema(env=block["env"], project_dir=root / block.get("project_dir", "."))


def _providers(path: Path, entries: object) -> tuple[tuple[Library, ...], dict[str, Provider]]:
    """``providers:``: shipped libraries by name, a project's own as ``module:attribute``.

    The module is imported, never evaluated; its attribute is a ``Library`` or a mapping
    of provider names to providers. A name two entries give is refused.
    """
    if not isinstance(entries, list):
        raise ProjectError(
            f"{path}: providers: lists shipped libraries and module:attribute entries"
        )
    libraries: list[Library] = []
    providers: dict[str, Provider] = {}
    named: set[str] = set()
    for entry in entries:
        found = _provider_entry(path, entry)
        names = found.named() if isinstance(found, Library) else found
        for name in names:
            if name in named:
                raise ProjectError(
                    f"{path}: providers: {name} is named twice",
                    resolution_hint="Enable each library once, and name a project's providers apart.",
                )
            named.add(name)
        if isinstance(found, Library):
            libraries.append(found)
        else:
            providers.update(found)
    return tuple(libraries), providers


def _provider_entry(path: Path, entry: object) -> Library | dict[str, Provider]:
    """One ``providers:`` entry: a library by name, or the attribute a module holds."""
    if not isinstance(entry, str):
        raise ProjectError(f"{path}: providers: {entry!r} is not a string")
    if ":" not in entry:
        return _library(path, entry)
    module, _, attribute = entry.partition(":")
    try:
        found = getattr(importlib.import_module(module), attribute, _MISSING)
    except ImportError as error:
        raise ProjectError(
            f"{path}: providers: {module} does not import: {error}",
            resolution_hint="Install the module, or put the directory that holds it on PYTHONPATH.",
        ) from error
    if found is _MISSING:
        raise ProjectError(
            f"{path}: providers: {module} has no {attribute}",
            resolution_hint=f"Name the attribute of {module} that holds the providers.",
        )
    if isinstance(found, Library):
        return found
    if not isinstance(found, Mapping):
        raise ProjectError(f"{path}: providers: {entry} is neither a Library nor a mapping")
    for name, provider in found.items():
        if not isinstance(name, str) or not callable(provider):
            raise ProjectError(
                f"{path}: providers: {entry} maps {name} to {provider!r}, not a provider"
            )
    return dict(found)


_MISSING = object()


def _library(path: Path, name: str) -> Library:
    """The library *name*: shipped, or installed under :data:`ENTRY_POINTS`.

    Only *name*'s entry point is loaded, so installing a library enables nothing until
    ``semis.yaml`` names it.
    """
    installed = sorted(entry_points(group=ENTRY_POINTS, name=name), key=_distribution)
    if name in SHIPPED:
        if installed:
            raise ProjectError(
                f"{path}: providers: {name} is shipped, and installed by "
                f"{_distribution(installed[0])} too",
                resolution_hint=f"Register {_distribution(installed[0])}'s library under another name.",
            )
        return SHIPPED[name]
    if not installed:
        available = sorted({*SHIPPED, *entry_points(group=ENTRY_POINTS).names})
        raise ProjectError(
            f"{path}: providers: {name} is neither shipped nor installed",
            resolution_hint=(
                f"Available: {', '.join(available)}; install the package that provides "
                f"{name}, or name a project's own providers package.module:ATTRIBUTE."
            ),
        )
    if len(installed) > 1:
        raise ProjectError(
            f"{path}: providers: {name} is installed by "
            f"{' and '.join(_distribution(point) for point in installed)}",
            resolution_hint="Uninstall all but one of them.",
        )
    return _load(path, installed[0])


def _load(path: Path, point: EntryPoint) -> Library:
    """The ``Library`` *point* names, refused unless it is one, under *point*'s name."""
    where = f"{path}: providers: {point.name}, installed by {_distribution(point)},"
    try:
        found = point.load()
    except (ImportError, AttributeError) as error:
        raise ProjectError(
            f"{where} does not import: {error}",
            resolution_hint=f"Reinstall {_distribution(point)}, or report it to its authors.",
        ) from error
    if not isinstance(found, Library):
        raise ProjectError(
            f"{where} is not a Library: {point.value}",
            resolution_hint=f"Report it to {_distribution(point)}'s authors, or uninstall it.",
        )
    if found.name != point.name:
        raise ProjectError(
            f"{where} holds the library {found.name}",
            resolution_hint="An entry point is named after the library it holds.",
        )
    return found


def _distribution(point: EntryPoint) -> str:
    return point.dist.name if point.dist is not None else point.value


def _prep_seed(path: Path, block: object) -> tuple[Staging, Path | None, str | None]:
    """``prep_seed:``: where the staging twins are, the schema tree holding the resolvers
    (resolved beside the project file), and the final tables' fallback schema."""
    if not isinstance(block, dict):
        raise ProjectError(f"{path}: prep_seed: is a mapping")
    refuse_unknown(f"{path}: prep_seed:", block, _PREP_SEED_KEYS, error=ProjectError)
    schema = block.get("prep_seed_schema", Staging.schema)
    if not isinstance(schema, str):
        raise ProjectError(f"{path}: prep_seed: prep_seed_schema: is a string")
    schema_dir = block.get("schema_dir")
    if schema_dir is not None:
        if not isinstance(schema_dir, str):
            raise ProjectError(f"{path}: prep_seed: schema_dir: is a string, a directory")
        schema_dir = path.parent / schema_dir
        if not schema_dir.is_dir():
            raise ProjectError(f"{path}: prep_seed: schema_dir: names {schema_dir}, no directory")
    catalog_schema = block.get("catalog_schema")
    if catalog_schema is not None and not isinstance(catalog_schema, str):
        raise ProjectError(f"{path}: prep_seed: catalog_schema: is a string")
    return Staging(schema), schema_dir, catalog_schema


def _table_codes(path: Path, value: object) -> TableCodes:
    """``table_codes:``, inline or in a file of their own beside the project file."""
    if isinstance(value, str):
        codes_path = path.parent / value
        if not codes_path.is_file():
            raise ProjectError(f"{path}: table_codes: names {codes_path}, which does not exist")
        value = read_yaml(codes_path, ProjectError, _PROJECT_YAML) or {}
    if not isinstance(value, dict):
        raise ProjectError(f"{path}: table_codes: maps a qualified table name to its hex code")
    for table, code in value.items():
        if type(code) is not int:
            raise ProjectError(f"{path}: {table}'s code is {code!r}, not an integer")
    return TableCodes(value)
