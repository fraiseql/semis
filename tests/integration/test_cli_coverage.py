"""``semis coverage``: every view a scenario reaches, its rows and tenants; nothing written."""

import json
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from typer.testing import CliRunner, Result

from fraiseql_semis.cli import app

SCHEMA = "semis_cli_coverage"
DDL = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {SCHEMA}.tb_org (
    pk_org BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);
CREATE TABLE {SCHEMA}.tb_item (
    pk_item BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    tenant_id UUID NOT NULL,
    fk_org BIGINT NOT NULL REFERENCES {SCHEMA}.tb_org (pk_org),
    name TEXT NOT NULL
);
CREATE VIEW {SCHEMA}.v_item AS SELECT id, tenant_id, name FROM {SCHEMA}.tb_item;
CREATE VIEW {SCHEMA}.v_org AS SELECT id, name FROM {SCHEMA}.tb_org;
CREATE MATERIALIZED VIEW {SCHEMA}.mv_org AS SELECT id FROM {SCHEMA}.tb_org;
"""
# A view scoped as a request's identity scopes it: by a session variable, read through one
# filter function, and empty while the variable is unset.
SCOPED = f"""
CREATE FUNCTION {SCHEMA}.fn_sees_all() RETURNS boolean LANGUAGE sql STABLE
    AS $$ SELECT current_setting('app.agent_role', true) = 'manager' $$;
CREATE VIEW {SCHEMA}.v_org_scoped AS
    SELECT id, name FROM {SCHEMA}.tb_org WHERE {SCHEMA}.fn_sees_all();
"""
AS_MANAGER = "coverage:\n  session_variables:\n    app.agent_role: manager\n    app.locale: fr\n"
CODES = f"""\
table_codes:
  {SCHEMA}.tb_org: 0x31323334
  {SCHEMA}.tb_item: 0x32333435
"""
TENANCY = f"""\
tenancy:
  column: tenant_id
  table: {SCHEMA}.tb_org
"""
FROM_DDL = "schema:\n  ddl: schema.sql\n"
FROM_DATABASE = f"schema:\n  database:\n    schemas: [{SCHEMA}]\n"


def _scenario(orgs: int) -> str:
    return f"""\
scenario_id: 0x5030
name: views
mode: read-back
seed: 42
tables:
  - name: {SCHEMA}.tb_org
    count: {orgs}
  - name: {SCHEMA}.tb_item
    count: 6
"""


NOT_FOLLOWED = "a view that reads a table only through a routine's body is not listed"
runner = CliRunner()


@pytest.fixture
def database(database_url: str) -> Iterator[str]:
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        admin.execute(DDL)
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


def _semis(  # noqa: PLR0913 — the project's parts, then the command line
    tmp_path: Path,
    database: str,
    command: str,
    *args: str,
    orgs: int = 2,
    project: str = FROM_DDL + CODES + TENANCY,
    ddl: str = DDL,
) -> Result:
    (tmp_path / "schema.sql").write_text(ddl)
    (tmp_path / "semis.yaml").write_text(project)
    path = tmp_path / "views.yaml"
    path.write_text(_scenario(orgs))
    config = ["-c", str(tmp_path / "semis.yaml"), "--database-url", database]
    return runner.invoke(app, [command, str(path), *config, *args])


def _applied(
    tmp_path: Path, database: str, *, orgs: int = 2, project: str = FROM_DDL + CODES + TENANCY
) -> None:
    result = _semis(tmp_path, database, "apply", orgs=orgs, project=project)
    assert result.exit_code == 0, result.output


def test_coverage_prints_each_view_with_its_rows_and_tenants(database: str, tmp_path: Path) -> None:
    _applied(tmp_path, database)
    result = _semis(tmp_path, database, "coverage")
    assert (result.exit_code, result.stdout.splitlines()) == (
        0,
        [
            "view                       rows  tenants",
            f"{SCHEMA}.mv_org     0        -  no rows",
            f"{SCHEMA}.v_item     6        2",
            f"{SCHEMA}.v_org      2        -",
            NOT_FOLLOWED,
        ],
    )


def test_coverage_as_json_is_a_list_of_objects(database: str, tmp_path: Path) -> None:
    _applied(tmp_path, database, orgs=1)
    result = _semis(tmp_path, database, "coverage", "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "view": f"{SCHEMA}.mv_org",
            "kind": "matview",
            "rows": 0,
            "tenants": None,
            "single_tenant": False,
        },
        {
            "view": f"{SCHEMA}.v_item",
            "kind": "view",
            "rows": 6,
            "tenants": 1,
            "single_tenant": True,
        },
        {
            "view": f"{SCHEMA}.v_org",
            "kind": "view",
            "rows": 1,
            "tenants": None,
            "single_tenant": False,
        },
    ]
    assert NOT_FOLLOWED in result.stderr


def test_coverage_exits_0_on_one_tenant_unless_asked_to_fail(database: str, tmp_path: Path) -> None:
    _applied(tmp_path, database, orgs=1)
    report = _semis(tmp_path, database, "coverage")
    failed = _semis(tmp_path, database, "coverage", "--fail-on-single-tenant")
    assert (report.exit_code, failed.exit_code) == (0, 1)
    assert f"{SCHEMA}.v_item     6        1  one tenant" in failed.stdout
    assert failed.stderr.splitlines() == [
        "1 view that outputs tenant_id holds fewer than two tenants"
    ]


def test_coverage_passes_the_fail_flag_on_two_tenants(database: str, tmp_path: Path) -> None:
    _applied(tmp_path, database)
    assert _semis(tmp_path, database, "coverage", "--fail-on-single-tenant").exit_code == 0


def test_coverage_writes_nothing(database: str, tmp_path: Path) -> None:
    result = _semis(tmp_path, database, "coverage")
    assert result.exit_code == 0, result.output
    assert _semis(tmp_path, database, "apply").exit_code == 0


def test_coverage_reads_a_database_schema_live(database: str, tmp_path: Path) -> None:
    project = FROM_DATABASE + CODES + TENANCY
    _applied(tmp_path, database, project=project)
    result = _semis(tmp_path, database, "coverage", project=project)
    assert result.exit_code == 0, result.output
    assert f"{SCHEMA}.v_item     6        2" in result.stdout.splitlines()


def test_without_tenancy_no_view_is_multi_tenant_and_the_report_exits_0(
    database: str, tmp_path: Path
) -> None:
    project = FROM_DDL + CODES
    _applied(tmp_path, database, project=project)
    result = _semis(tmp_path, database, "coverage", project=project)
    assert result.exit_code == 0, result.output
    assert f"{SCHEMA}.v_item     6        -" in result.stdout.splitlines()


def test_the_fail_flag_without_tenancy_is_refused(database: str, tmp_path: Path) -> None:
    result = _semis(
        tmp_path, database, "coverage", "--fail-on-single-tenant", project=FROM_DDL + CODES
    )
    assert result.exit_code == 1
    assert "semis.yaml names no tenancy: column" in result.stderr


@pytest.mark.parametrize(
    ("kind", "create"),
    [
        ("view", "CREATE VIEW {name} AS"),
        ("matview", "CREATE MATERIALIZED VIEW {name} AS"),
        ("tview", "CREATE TABLE {name} AS"),
    ],
)
def test_a_view_whose_columns_cannot_be_named_is_refused_naming_it(
    database: str, tmp_path: Path, kind: str, create: str
) -> None:
    name = f"{SCHEMA}.{'tv' if kind == 'tview' else 'v'}_star"
    broken = DDL + create.format(name=name) + f" SELECT * FROM {SCHEMA}.tb_item, elsewhere;"
    result = _semis(tmp_path, database, "coverage", ddl=broken)
    assert result.exit_code == 4
    assert f"{kind} {name}'s columns cannot be named" in result.stderr
    assert "Traceback" not in result.output


UNFILLED = (
    f"CREATE MATERIALIZED VIEW {SCHEMA}.mv_unfilled AS SELECT id FROM {SCHEMA}.tb_org WITH NO DATA;"
)


def test_a_view_postgresql_refuses_to_read_is_refused_naming_it(
    database: str, tmp_path: Path
) -> None:
    with psycopg.connect(database, autocommit=True) as admin:
        admin.execute(UNFILLED)
    result = _semis(tmp_path, database, "coverage", ddl=DDL + UNFILLED)
    assert result.exit_code == 1
    assert result.stderr.splitlines() == [
        f'matview {SCHEMA}.mv_unfilled cannot be read: materialized view "mv_unfilled" has not '
        "been populated",
        "Hint: Use the REFRESH MATERIALIZED VIEW command.",
    ]
    assert "Traceback" not in result.output


@pytest.fixture
def scoped(database: str) -> str:
    with psycopg.connect(database, autocommit=True) as admin:
        admin.execute(SCOPED)
    return database


def _scoped_rows(result: Result) -> str:
    return next(line for line in result.stdout.splitlines() if "v_org_scoped" in line)


def test_a_view_scoped_by_an_unset_session_variable_shows_no_rows(
    scoped: str, tmp_path: Path
) -> None:
    _applied(tmp_path, scoped)
    result = _semis(tmp_path, scoped, "coverage", ddl=DDL + SCOPED)
    assert result.exit_code == 0, result.output
    assert _scoped_rows(result) == f"{SCHEMA}.v_org_scoped     0        -  no rows"
    assert result.stdout.splitlines()[0].startswith("view ")


def test_coverage_reads_with_the_projects_session_variables(scoped: str, tmp_path: Path) -> None:
    _applied(tmp_path, scoped)
    project = FROM_DDL + CODES + TENANCY + AS_MANAGER
    result = _semis(tmp_path, scoped, "coverage", project=project, ddl=DDL + SCOPED)
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == "read with app.agent_role='manager', app.locale='fr'"
    assert _scoped_rows(result) == f"{SCHEMA}.v_org_scoped     2        -"


def test_coverage_as_json_says_what_it_read_with_on_stderr(scoped: str, tmp_path: Path) -> None:
    _applied(tmp_path, scoped)
    project = FROM_DDL + CODES + TENANCY + AS_MANAGER
    result = _semis(
        tmp_path, scoped, "coverage", "--format", "json", project=project, ddl=DDL + SCOPED
    )
    assert result.exit_code == 0, result.output
    rows = {view["view"]: view["rows"] for view in json.loads(result.stdout)}
    assert rows[f"{SCHEMA}.v_org_scoped"] == 2
    assert result.stderr.splitlines() == [
        "read with app.agent_role='manager', app.locale='fr'",
        NOT_FOLLOWED,
    ]


def test_a_built_in_setting_under_session_variables_is_refused(
    database: str, tmp_path: Path
) -> None:
    project = FROM_DDL + CODES + "coverage:\n  session_variables:\n    role: admin\n"
    result = _semis(tmp_path, database, "coverage", project=project)
    assert result.exit_code == 1
    assert "coverage: session_variables: role is not a custom setting" in result.stderr
    assert "Traceback" not in result.output
