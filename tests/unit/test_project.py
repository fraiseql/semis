"""semis.yaml: the schema, the table codes and the scenarios a project's commands read."""

from pathlib import Path

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ProjectError
from fraiseql_semis.project import Project, ProjectSchema
from fraiseql_semis.providers import SHIPPED
from fraiseql_semis.schema import ConfigurationError, SchemaFacts
from fraiseql_semis.staging import Staging
from tests.ddl import CODES, TRINITY
from tests.unit import fake_providers

SEMIS_YAML = """\
schema:
  ddl: db/schema.sql
scenarios: seeds/scenarios
table_codes:
  catalog.tb_continent: 0x02030405
  catalog.tb_country: 0x03040506
"""


def _project(tmp_path: Path, text: str = SEMIS_YAML) -> Path:
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "schema.sql").write_text(TRINITY)
    path = tmp_path / "semis.yaml"
    path.write_text(text)
    return path


def test_semis_yaml_loads_codes_schema_and_scenarios(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path))
    facts = project.facts()
    assert (
        project.scenarios,
        project.table_codes.code_for("catalog.tb_country"),
        facts.facts_for("catalog.tb_continent").table_code,
        facts.source_kind,
    ) == (tmp_path / "seeds" / "scenarios", CODES["catalog.tb_country"], 0x02030405, "ddl")


def test_table_codes_may_be_a_file_of_their_own(tmp_path: Path) -> None:
    (tmp_path / "table_codes.yaml").write_text("catalog.tb_continent: 0x02030405\n")
    text = "schema:\n  ddl: db/schema.sql\ntable_codes: table_codes.yaml\n"
    project = Project.load(_project(tmp_path, text))
    assert project.table_codes.table_for(0x02030405) == "catalog.tb_continent"


def test_scenarios_default_to_a_directory_beside_the_file(tmp_path: Path) -> None:
    text = "schema:\n  ddl: db/schema.sql\ntable_codes: {}\n"
    assert Project.load(_project(tmp_path, text)).scenarios == tmp_path / "scenarios"


def test_env_is_read_in_the_named_project_dir(tmp_path: Path) -> None:
    text = "schema:\n  env: development\n  project_dir: app\ntable_codes: {}\n"
    project = Project.load(_project(tmp_path, text))
    assert project.schema == ProjectSchema(env="development", project_dir=tmp_path / "app")


def test_env_without_project_dir_is_the_files_directory(tmp_path: Path) -> None:
    text = "schema:\n  env: development\ntable_codes: {}\n"
    project = Project.load(_project(tmp_path, text))
    assert project.schema == ProjectSchema(env="development", project_dir=tmp_path)


def test_database_names_the_schemas_it_introspects(tmp_path: Path) -> None:
    text = "schema:\n  database:\n    schemas: [catalog, tenant]\ntable_codes: {}\n"
    project = Project.load(_project(tmp_path, text))
    assert project.schema == ProjectSchema(schemas=("catalog", "tenant"))


def test_a_database_schema_needs_a_url_to_be_read() -> None:
    with pytest.raises(ProjectError, match="reads its schema from a database"):
        ProjectSchema(schemas=("catalog",)).read(TableCodes({}))


def test_a_database_project_with_no_url_anywhere_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("DATABASE_URL", "CONFITURE_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    text = "schema:\n  database:\n    schemas: [catalog]\ntable_codes: {}\n"
    with pytest.raises(ProjectError, match="no database URL"):
        Project.load(_project(tmp_path, text)).facts()


def test_a_schema_built_in_python_names_one_source() -> None:
    with pytest.raises(ProjectError, match="exactly one of ddl, env, schemas"):
        ProjectSchema(ddl=Path("a.sql"), env="development")


def test_facts_given_in_python_are_used_as_they_are() -> None:
    facts = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))
    project = Project(table_codes=TableCodes(CODES), schema=facts, scenarios=Path("scenarios"))
    assert project.facts() is facts


@pytest.mark.parametrize(
    ("text", "refusal"),
    [
        ("- not a mapping\n", "holds no mapping"),
        ("schema: {ddl: a.sql}\n", "has no table_codes:"),
        ("table_codes: {}\n", "has no schema:"),
        ("schema: {ddl: a.sql}\ntable_codes: {}\nscenario: s\n", "unknown key scenario"),
        ("schema: a.sql\ntable_codes: {}\n", "schema: is a mapping"),
        ("schema: {ddl: a.sql, env: dev}\ntable_codes: {}\n", "exactly one of"),
        ("schema: {}\ntable_codes: {}\n", "exactly one of"),
        ("schema: {ddl: a.sql, schemas: [x]}\ntable_codes: {}\n", "unknown key schemas"),
        ("schema: {ddl: a.sql, project_dir: x}\ntable_codes: {}\n", "project_dir: goes with env:"),
        ("schema: {database: [x]}\ntable_codes: {}\n", "database: names its schemas:"),
        ("schema: {database: {schemas: x}}\ntable_codes: {}\n", "database: names its schemas:"),
        ("schema: {ddl: 3}\ntable_codes: {}\n", "schema: ddl: is a string"),
        ("schema: {ddl: a.sql}\ntable_codes: [1]\n", "table_codes: maps"),
        ("schema: {ddl: a.sql}\ntable_codes: {catalog.t: '7'}\n", "catalog.t's code is"),
        ("schema: {ddl: a.sql}\ntable_codes: missing.yaml\n", "missing.yaml, which does not"),
        ("schema: {ddl: a.sql}\ntable_codes: {}\nscenarios: 3\n", "scenarios: is a string"),
        ("schema: {ddl: a.sql}\ntable_codes: {}\nprep_seed: x\n", "prep_seed: is a mapping"),
        ("schema: {ddl: a.sql}\ntable_codes: {}\nprep_seed: {x: 1}\n", "prep_seed:: unknown key x"),
        (
            "schema: {ddl: a.sql}\ntable_codes: {}\nprep_seed: {prep_seed_schema: 3}\n",
            "prep_seed_schema: is a string",
        ),
        (
            "schema: {ddl: a.sql}\ntable_codes: {}\nprep_seed: {schema_dir: 3}\n",
            "schema_dir: is a string, a directory",
        ),
        (
            "schema: {ddl: a.sql}\ntable_codes: {}\nprep_seed: {schema_dir: nowhere}\n",
            "nowhere, no directory",
        ),
        (
            "schema: {ddl: a.sql}\ntable_codes: {}\nprep_seed: {catalog_schema: [x]}\n",
            "catalog_schema: is a string",
        ),
    ],
)
def test_a_malformed_project_file_is_refused_naming_the_key(
    tmp_path: Path, text: str, refusal: str
) -> None:
    with pytest.raises(ProjectError, match=refusal):
        Project.load(_project(tmp_path, text))


def test_an_absent_project_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="no project file at"):
        Project.load(tmp_path / "semis.yaml")


def test_find_reads_semis_yaml_in_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    found = Project.find(None)
    assert found is not None
    assert found.scenarios == Path("seeds/scenarios")


def test_find_without_a_project_file_finds_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert Project.find(None) is None


def test_an_env_schema_is_confitures_build(tmp_path: Path) -> None:
    (tmp_path / "db" / "environments").mkdir(parents=True)
    (tmp_path / "db" / "environments" / "development.yaml").write_text(
        "name: development\ndatabase_url: postgresql:///unused\ninclude_dirs: [db/schema]\n"
    )
    (tmp_path / "db" / "schema").mkdir()
    (tmp_path / "db" / "schema" / "trinity.sql").write_text(TRINITY)
    (tmp_path / "semis.yaml").write_text("schema:\n  env: development\ntable_codes: {}\n")
    facts = Project.load(tmp_path / "semis.yaml").facts()
    assert [ref.display for ref in facts.insert_order()] == [
        "catalog.tb_continent",
        "catalog.tb_country",
        "prep_seed.tb_continent",
        "prep_seed.tb_country",
    ]


def test_a_database_schema_is_introspected_at_the_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        SchemaFacts,
        "from_database",
        lambda url, *, schemas, table_codes: calls.append((url, schemas, table_codes)),
    )
    text = "schema:\n  database:\n    schemas: [catalog]\ntable_codes: {}\n"
    project = Project.load(_project(tmp_path, text))
    project.facts(database_url="postgresql:///db")
    assert calls == [("postgresql:///db", ("catalog",), project.table_codes)]


@pytest.fixture
def no_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CONFITURE_DATABASE_URL", raising=False)


@pytest.mark.usefixtures("no_urls")
def test_an_env_project_connects_to_its_environments_database(tmp_path: Path) -> None:
    (tmp_path / "db" / "environments").mkdir(parents=True)
    (tmp_path / "db" / "schema").mkdir()
    (tmp_path / "db" / "environments" / "development.yaml").write_text(
        "name: development\ndatabase_url: postgresql:///from_env\ninclude_dirs: [db/schema]\n"
    )
    (tmp_path / "semis.yaml").write_text("schema:\n  env: development\ntable_codes: {}\n")
    project = Project.load(tmp_path / "semis.yaml")
    assert project.database_url(mutating=True) == "postgresql:///from_env"


@pytest.mark.usefixtures("no_urls")
def test_a_flag_wins_over_the_project(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path))
    assert project.database_url("postgresql:///flag", mutating=True) == "postgresql:///flag"


@pytest.mark.usefixtures("no_urls")
def test_a_ddl_project_with_no_url_is_refused_when_it_connects(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="no database URL"):
        Project.load(_project(tmp_path)).database_url(mutating=True)


@pytest.mark.usefixtures("no_urls")
def test_a_command_that_writes_refuses_the_ambient_url_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql:///ambient")
    project = Project.load(_project(tmp_path))
    with pytest.raises(ConfigurationError, match="ambient DATABASE_URL"):
        project.database_url(mutating=True)
    assert project.database_url(mutating=False) == "postgresql:///ambient"


def test_providers_name_libraries_and_a_module(tmp_path: Path) -> None:
    text = SEMIS_YAML + (
        "providers:\n"
        "  - i18n\n"
        "  - tests.unit.fake_providers:PROVIDERS\n"
        "  - tests.unit.fake_providers:LIBRARY\n"
    )
    project = Project.load(_project(tmp_path, text))
    assert (project.libraries, project.providers) == (
        (SHIPPED["i18n"], fake_providers.LIBRARY),
        {"slogan": fake_providers.slogan},
    )


_MODULE = "tests.unit.fake_providers"


@pytest.mark.parametrize(
    ("providers", "refusal"),
    [
        ("i18n", r"providers: lists"),
        ("[3]", r"providers: 3 is not a string"),
        ("[nope]", r"providers: nope is neither shipped nor installed"),
        ("[no.such.module:X]", r"providers: no.such.module does not import"),
        (f"[{_MODULE}:MISSING]", rf"providers: {_MODULE} has no MISSING"),
        (f"[{_MODULE}:NOT_PROVIDERS]", r"NOT_PROVIDERS is neither a Library nor a mapping"),
        (f"[{_MODULE}:BAD_MAPPING]", r"BAD_MAPPING maps broken to 42, not a provider"),
        ("[i18n, i18n]", r"providers: i18n.country_code is named twice"),
        (f"[i18n, {_MODULE}:SHADOWING]", r"providers: i18n.country_code is named twice"),
    ],
)
def test_a_malformed_providers_entry_is_refused_naming_it(
    tmp_path: Path, providers: str, refusal: str
) -> None:
    with pytest.raises(ProjectError, match=refusal):
        Project.load(_project(tmp_path, SEMIS_YAML + f"providers: {providers}\n"))


def test_a_module_that_does_not_import_is_refused_saying_how_it_imports(tmp_path: Path) -> None:
    """The semis command's import path is its own installation's, not the directory it
    runs in: a project's module imports when installed, or on PYTHONPATH."""
    with pytest.raises(ProjectError) as refused:
        Project.load(_project(tmp_path, SEMIS_YAML + "providers: [no.such.module:X]\n"))
    assert refused.value.resolution_hint == (
        "Install the module, or put the directory that holds it on PYTHONPATH."
    )


def _install(root: Path, distribution: str, entries: dict[str, str]) -> None:
    """*distribution*, installed as far as ``importlib.metadata`` can tell, under *root*."""
    info = root / f"{distribution.replace('-', '_')}-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {distribution}\nVersion: 1.0\n")
    lines = "".join(f"{name} = {value}\n" for name, value in entries.items())
    (info / "entry_points.txt").write_text(f"[fraiseql_semis.providers]\n{lines}")


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A directory on ``sys.path`` that distributions are installed into."""
    root = tmp_path / "site"
    root.mkdir()
    monkeypatch.syspath_prepend(str(root))
    return root


def test_an_installed_library_is_enabled_by_its_name(tmp_path: Path, site: Path) -> None:
    _install(site, "acme-semis", {"acme": f"{_MODULE}:LIBRARY"})
    project = Project.load(_project(tmp_path, SEMIS_YAML + "providers: [i18n, acme]\n"))
    assert project.libraries == (SHIPPED["i18n"], fake_providers.LIBRARY)


def test_only_the_named_entry_point_is_imported(tmp_path: Path, site: Path) -> None:
    _install(site, "acme-semis", {"acme": f"{_MODULE}:LIBRARY"})
    _install(site, "broken-semis", {"broken": "no.such.module:LIBRARY"})
    project = Project.load(_project(tmp_path, SEMIS_YAML + "providers: [acme]\n"))
    assert project.libraries == (fake_providers.LIBRARY,)


@pytest.mark.parametrize(
    ("installed", "providers", "refusal"),
    [
        (
            {"acme-semis": {"acme": f"{_MODULE}:LIBRARY"}, "acme-fork": {"acme": "x:Y"}},
            "[acme]",
            r"providers: acme is installed by acme-fork and acme-semis",
        ),
        (
            {"acme-semis": {"i18n": f"{_MODULE}:LIBRARY"}},
            "[i18n]",
            r"providers: i18n is shipped, and installed by acme-semis too",
        ),
        (
            {"acme-semis": {"acme": f"{_MODULE}:PROVIDERS"}},
            "[acme]",
            r"providers: acme, installed by acme-semis, is not a Library",
        ),
        (
            {"acme-semis": {"other": f"{_MODULE}:LIBRARY"}},
            "[other]",
            r"providers: other, installed by acme-semis, holds the library acme",
        ),
        (
            {"acme-semis": {"acme": "no.such.module:LIBRARY"}},
            "[acme]",
            r"providers: acme, installed by acme-semis, does not import",
        ),
    ],
)
def test_an_installed_library_is_refused_naming_its_distribution(
    tmp_path: Path, site: Path, installed: dict[str, dict[str, str]], providers: str, refusal: str
) -> None:
    for distribution, entries in installed.items():
        _install(site, distribution, entries)
    with pytest.raises(ProjectError, match=refusal):
        Project.load(_project(tmp_path, SEMIS_YAML + f"providers: {providers}\n"))


def test_the_manager_carries_the_projects_providers(tmp_path: Path) -> None:
    text = SEMIS_YAML + f"providers: [i18n, {_MODULE}:PROVIDERS]\n"
    scenario = tmp_path / "s.yaml"
    scenario.write_text(
        "scenario_id: 0x5001\nname: s\nmode: read-back\ntables:\n"
        "  - name: catalog.tb_continent\n    count: 1\n"
        "    providers: {name: slogan, identifier: i18n.country_code}\n"
    )
    manager = Project.load(_project(tmp_path, text)).manager()
    assert manager.load(scenario).tables[0].providers == {
        "name": "slogan",
        "identifier": "i18n.country_code",
    }


def test_prep_seed_names_the_staging_schema(tmp_path: Path) -> None:
    text = SEMIS_YAML + "prep_seed:\n  prep_seed_schema: staging\n"
    assert Project.load(_project(tmp_path, text)).staging == Staging("staging")
    (tmp_path / "default").mkdir()
    assert Project.load(_project(tmp_path / "default")).staging == Staging("prep_seed")


def test_prep_seed_names_the_resolvers_tree_and_the_catalog_fallback(tmp_path: Path) -> None:
    text = SEMIS_YAML + "prep_seed:\n  schema_dir: db\n  catalog_schema: tenant\n"
    project = Project.load(_project(tmp_path, text))
    assert (project.resolvers(), project.catalog_schema) == (tmp_path / "db", "tenant")


def test_a_ddl_directory_holds_the_resolvers_unless_named(tmp_path: Path) -> None:
    project = Project.load(_project(tmp_path, SEMIS_YAML.replace("db/schema.sql", "db")))
    assert (project.resolvers(), project.catalog_schema) == (tmp_path / "db", None)


def test_a_ddl_file_names_no_resolvers_tree(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="no schema directory to read the resolvers from"):
        Project.load(_project(tmp_path)).resolvers()


def test_a_project_key_that_is_not_a_string_is_refused_naming_it(tmp_path: Path) -> None:
    path = tmp_path / "semis.yaml"
    path.write_text("schema: {ddl: a.sql}\ntable_codes: {}\n1: x\n")
    with pytest.raises(ProjectError) as refused:
        Project.load(path)
    assert str(refused.value).splitlines()[0] == f"{path}: unknown key 1"


def test_a_project_file_that_does_not_read_as_yaml_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "semis.yaml"
    path.write_text("schema: {ddl: a.sql\n")
    with pytest.raises(ProjectError) as refused:
        Project.load(path)
    assert str(refused.value).splitlines()[0].startswith(f"{path} does not read as YAML: ")
