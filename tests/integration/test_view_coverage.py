"""``semis coverage``'s reads: each view's rows and tenants, in a read-only transaction."""

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import LiteralString

import psycopg
import pytest
from psycopg import sql

from fraiseql_semis import ScenarioManager, readback
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ProjectError, SchemaNotBuiltError, ViewReadError
from fraiseql_semis.schema import ObjectRef, SchemaFacts
from fraiseql_semis.tenancy import TenancyRule
from fraiseql_semis.view_coverage import ViewCount

SCHEMA = "semis_views"
DDL = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {SCHEMA}."tb Item" (pk_item bigint PRIMARY KEY, id uuid NOT NULL, tenant_id uuid);
CREATE VIEW {SCHEMA}."v Item" AS SELECT id, tenant_id FROM {SCHEMA}."tb Item";
"""
ROWS = f"""
INSERT INTO {SCHEMA}."tb Item" VALUES
  (1, gen_random_uuid(), '00000000-0000-0000-0000-00000000000a'),
  (2, gen_random_uuid(), '00000000-0000-0000-0000-00000000000a'),
  (3, gen_random_uuid(), '00000000-0000-0000-0000-00000000000b'),
  (4, gen_random_uuid(), NULL);
"""


@pytest.fixture
def database(database_url: str) -> Iterator[str]:
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        admin.execute(DDL + ROWS)
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {SCHEMA} CASCADE")


def _view(name: str) -> ObjectRef:
    facts = SchemaFacts.from_source(DDL, table_codes=TableCodes({}))
    return next(ref for ref in facts.views() if ref.name == name)


def test_a_view_counts_its_rows_and_distinct_tenants(database: str) -> None:
    with readback.transaction(database, commit=False) as connection:
        assert readback.count_view(connection, _view("v Item"), "tenant_id") == (4, 2)


def test_a_view_without_a_tenant_column_counts_its_rows_alone(database: str) -> None:
    with readback.transaction(database, commit=False) as connection:
        assert readback.count_view(connection, _view("v Item"), None) == (4, None)


def test_a_view_the_database_lacks_is_refused(database_url: str) -> None:
    with (
        readback.transaction(database_url, commit=False) as connection,
        pytest.raises(SchemaNotBuiltError, match=r"holds no semis_views\.v Item"),
    ):
        readback.count_view(connection, _view("v Item"), None)


# Views PostgreSQL refuses to read: a materialized view never refreshed, and a view
# reading a setting without missing_ok while it is unset, and one that raises as it is read.
UNREADABLE = f"""
CREATE MATERIALIZED VIEW {SCHEMA}.mv_unfilled AS SELECT id FROM {SCHEMA}."tb Item" WITH NO DATA;
CREATE VIEW {SCHEMA}.v_strict AS
    SELECT id FROM {SCHEMA}."tb Item" WHERE current_setting('app.tenant') <> '';
CREATE VIEW {SCHEMA}.v_broken AS SELECT id FROM {SCHEMA}."tb Item" WHERE 1 / (pk_item - pk_item) = 0;
"""


def _unreadable(name: str) -> ObjectRef:
    facts = SchemaFacts.from_source(DDL + UNREADABLE, table_codes=TableCodes({}))
    return next(ref for ref in facts.views() if ref.name == name)


@pytest.mark.parametrize(
    ("name", "said", "hint"),
    [
        (
            "mv_unfilled",
            r'matview semis_views\.mv_unfilled cannot be read: materialized view "mv_unfilled" '
            r"has not been populated",
            "REFRESH MATERIALIZED VIEW",
        ),
        (
            "v_strict",
            r'view semis_views\.v_strict cannot be read: unrecognized configuration parameter "app'
            r'\.tenant"',
            "coverage: session_variables:",
        ),
        (
            "v_broken",
            r"view semis_views\.v_broken cannot be read: division by zero",
            "Read the view with psql",
        ),
    ],
)
def test_a_view_postgresql_refuses_to_read_is_refused_naming_it(
    database: str, name: str, said: str, hint: str
) -> None:
    with psycopg.connect(database, autocommit=True) as admin:
        admin.execute(UNREADABLE)
    with (
        readback.transaction(database, commit=False, read_only=True) as connection,
        pytest.raises(ViewReadError, match=said) as refused,
    ):
        readback.count_view(connection, _unreadable(name), None)
    assert hint in str(refused.value.resolution_hint)


def test_a_read_only_transaction_refuses_a_write(database: str) -> None:
    with (
        readback.transaction(database, commit=False, read_only=True) as connection,
        pytest.raises(psycopg.errors.ReadOnlySqlTransaction),
    ):
        connection.execute(f'DELETE FROM {SCHEMA}."tb Item"')


APP = "semis_coverage"
APP_DDL = f"""
CREATE SCHEMA {APP};
CREATE TABLE {APP}.tb_org (
    pk_org BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);
CREATE TABLE {APP}.tb_item (
    pk_item BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE,
    identifier TEXT NOT NULL UNIQUE,
    tenant_id UUID NOT NULL,
    fk_org BIGINT NOT NULL REFERENCES {APP}.tb_org (pk_org),
    name TEXT NOT NULL
);
CREATE TABLE {APP}.tb_other (
    pk_other BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id UUID NOT NULL UNIQUE
);
CREATE VIEW {APP}.v_item AS SELECT id, tenant_id, name FROM {APP}.tb_item;
CREATE VIEW {APP}.v_item_org AS
    SELECT v.id, v.tenant_id, o.name AS org
    FROM {APP}.v_item v JOIN {APP}.tb_item i USING (id) JOIN {APP}.tb_org o ON o.pk_org = i.fk_org;
CREATE VIEW {APP}.v_org AS SELECT id, name FROM {APP}.tb_org;
CREATE VIEW {APP}.v_other AS SELECT id FROM {APP}.tb_other;
CREATE MATERIALIZED VIEW {APP}.mv_org AS SELECT id FROM {APP}.tb_org;
"""
APP_CODES = {f"{APP}.tb_org": 0x21222324, f"{APP}.tb_item": 0x22232425}
RULE = TenancyRule("tenant_id", table=f"{APP}.tb_org")


def _scenario(orgs: int) -> str:
    return f"""\
scenario_id: 0x5020
name: views
mode: read-back
seed: 42
tables:
  - name: {APP}.tb_org
    count: {orgs}
  - name: {APP}.tb_item
    count: 6
"""


@pytest.fixture
def app(database_url: str) -> Iterator[psycopg.Connection]:
    """The schema in a throwaway schema, and the caller's connection, rolled back."""
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {APP} CASCADE")
        admin.execute(APP_DDL)
    with psycopg.connect(database_url) as connection:
        yield connection
        connection.rollback()
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {APP} CASCADE")


def _coverage(
    connection: psycopg.Connection,
    tmp_path: Path,
    ddl: str | SchemaFacts,
    orgs: int,
    session_variables: Mapping[str, str] | None = None,
) -> tuple[ViewCount, ...]:
    facts = ddl if isinstance(ddl, SchemaFacts) else _from_ddl(ddl)
    manager = ScenarioManager(facts, tenancy=RULE)
    path = tmp_path / "views.yaml"
    path.write_text(_scenario(orgs))
    scenario = manager.load(path)
    manager.apply(scenario, None, connection=connection)
    return manager.coverage(scenario, connection=connection, session_variables=session_variables)


def _from_ddl(ddl: str) -> SchemaFacts:
    return SchemaFacts.from_source(ddl, table_codes=TableCodes(APP_CODES))


def test_coverage_counts_every_view_a_written_table_reaches(
    app: psycopg.Connection, tmp_path: Path
) -> None:
    assert _coverage(app, tmp_path, APP_DDL, orgs=2) == (
        ViewCount(f"{APP}.mv_org", "matview", 0, None),
        ViewCount(f"{APP}.v_item", "view", 6, 2),
        ViewCount(f"{APP}.v_item_org", "view", 6, 2),
        ViewCount(f"{APP}.v_org", "view", 2, None),
    )


def test_one_tenant_is_counted_as_one(app: psycopg.Connection, tmp_path: Path) -> None:
    counts = _coverage(app, tmp_path, APP_DDL, orgs=1)
    assert [count.view for count in counts if count.single_tenant] == [
        f"{APP}.v_item",
        f"{APP}.v_item_org",
    ]


# A view scoped as a request's identity scopes it: by a session variable, read through one
# filter function, and empty while the variable is unset.
SCOPED = f"""
CREATE FUNCTION {APP}.fn_sees_all() RETURNS boolean LANGUAGE sql STABLE
    AS $$ SELECT current_setting('app.agent_role', true) = 'manager' $$;
CREATE VIEW {APP}.v_org_scoped AS SELECT id, name FROM {APP}.tb_org WHERE {APP}.fn_sees_all();
"""
MANAGER = {"app.agent_role": "manager"}


def test_a_view_scoped_by_a_session_variable_shows_no_rows_while_it_is_unset(
    app: psycopg.Connection, tmp_path: Path
) -> None:
    app.execute(SCOPED)
    counts = _coverage(app, tmp_path, APP_DDL + SCOPED, orgs=2)
    assert ViewCount(f"{APP}.v_org_scoped", "view", 0, None) in counts


def test_a_view_scoped_by_a_session_variable_shows_every_row_once_it_is_set(
    app: psycopg.Connection, tmp_path: Path
) -> None:
    app.execute(SCOPED)
    counts = _coverage(app, tmp_path, APP_DDL + SCOPED, orgs=2, session_variables=MANAGER)
    assert ViewCount(f"{APP}.v_org_scoped", "view", 2, None) in counts


def test_the_session_variables_end_with_the_callers_transaction(
    app: psycopg.Connection, tmp_path: Path
) -> None:
    app.execute(SCOPED)
    _coverage(app, tmp_path, APP_DDL + SCOPED, orgs=2, session_variables=MANAGER)
    assert app.execute("SELECT current_setting('app.agent_role', true)").fetchone() == ("manager",)
    app.rollback()
    assert app.execute("SELECT current_setting('app.agent_role', true)").fetchone() != ("manager",)


def test_a_session_variable_semis_would_refuse_is_never_set(
    app: psycopg.Connection, tmp_path: Path
) -> None:
    with pytest.raises(ProjectError, match="session_variables: role is not a custom setting"):
        _coverage(app, tmp_path, APP_DDL, orgs=2, session_variables={"role": "x"})
    app.rollback()
    assert app.execute("SELECT current_setting('role')").fetchone() == ("none",)


TVIEW = f"CREATE TABLE {APP}.tv_item AS SELECT pk_item, id, tenant_id, name FROM {APP}.tb_item;"


@pytest.mark.parametrize("live", [False, True], ids=["ddl", "live"])
def test_a_tview_holds_the_rows_its_tables_were_written(
    app: psycopg.Connection, database_url: str, tmp_path: Path, live: bool
) -> None:
    """pg_tviews keeps a TVIEW in step with its tables: the rows a run wrote are there,
    with the schema read from DDL or from the database. Run where the server has
    pg_tviews; skipped where it does not."""
    with psycopg.connect(database_url, autocommit=True) as admin:
        if "pg_tviews" not in _extensions(admin, "pg_available_extensions", "name"):
            pytest.skip("the server has no pg_tviews")
        before = _extensions(admin)
        admin.execute("CREATE EXTENSION IF NOT EXISTS pg_tviews CASCADE")
        try:
            # From 0.1.0-beta.30, pg_tviews keeps its objects in a schema of its own, and
            # confiture reads no older one.
            if admin.execute("SELECT to_regclass('tviews.registry')").fetchone() == (None,):
                pytest.skip("the server's pg_tviews is older than 0.1.0-beta.30")
            admin.execute(TVIEW)
            facts = _from_ddl(APP_DDL + TVIEW)
            if live:
                codes = TableCodes(APP_CODES)
                facts = SchemaFacts.from_database(database_url, schemas=[APP], table_codes=codes)
            counts = _coverage(app, tmp_path, facts, orgs=2)
        finally:
            app.rollback()
            admin.execute(f"DROP TABLE IF EXISTS {APP}.tv_item CASCADE")
            for name in sorted(_extensions(admin) - before):
                admin.execute(
                    sql.SQL("DROP EXTENSION IF EXISTS {} CASCADE").format(sql.Identifier(name))
                )
    assert ViewCount(f"{APP}.tv_item", "tview", 6, 2) in counts


def _extensions(
    admin: psycopg.Connection,
    catalog: LiteralString = "pg_extension",
    column: LiteralString = "extname",
) -> set[str]:
    query = sql.SQL("SELECT {} FROM {}").format(sql.Identifier(column), sql.Identifier(catalog))
    return {name for (name,) in admin.execute(query).fetchall()}
