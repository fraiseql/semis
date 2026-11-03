"""Tenants: a scenario spreads its tenant-scoped rows over them (fraiseql/semis#8)."""

import re
from pathlib import Path
from uuid import UUID

import pytest

from fraiseql_semis import emit
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ResolutionError, ScenarioError, SemisError
from fraiseql_semis.generator import Copied, FakeDataGenerator, Row, TablePlan
from fraiseql_semis.hierarchy import Hierarchy
from fraiseql_semis.resolution import PrepSeedResolver
from fraiseql_semis.scenario import ScenarioManager
from fraiseql_semis.schema import SchemaFacts
from fraiseql_semis.tenancy import Spread, TenancyRule, Tenants, spread
from fraiseql_semis.translation import Locale, Translation, TranslationRule, TranslationTable
from tests.ddl import TENANTS, TENANTS_CODES

FACTS = SchemaFacts.from_source(TENANTS, table_codes=TableCodes(TENANTS_CODES))
RULE = TenancyRule("tenant_id", table="app.tb_organization", exclude=frozenset({"app.tb_audit"}))
A, B = "0000000a-0000-4000-8000-000000000000", "0000000b-0000-4000-8000-000000000000"


def _load(tmp_path: Path, text: str, *, rule: TenancyRule | None = RULE) -> ScenarioManager:
    (tmp_path / "s.yaml").write_text(text)
    return ScenarioManager(FACTS, tenancy=rule, providers={"uuid4": lambda faker, _: faker.uuid4()})


def _file(body: str, *, head: str = "", mode: str = "read-back") -> str:
    return f"scenario_id: 0x5010\nname: s\nmode: {mode}\nseed: 42\n{head}tables:\n{body}"


PROJECTS = "  - name: app.tb_project\n    count: 4\n"


def test_tenants_load_as_the_scenarios_values(tmp_path: Path) -> None:
    manager = _load(tmp_path, _file(PROJECTS, head=f"tenants: [{A}, {B}]\n"))
    assert manager.load(tmp_path / "s.yaml").tenants == (A, B)


def test_crosses_tenants_loads_per_table(tmp_path: Path) -> None:
    body = "  - name: app.tb_task\n    count: 2\n    crosses_tenants: [fk_assignee]\n"
    manager = _load(tmp_path, _file(PROJECTS + body))
    spec = manager.load(tmp_path / "s.yaml").tables[1]
    assert spec.crosses_tenants == frozenset({"fk_assignee"})


@pytest.mark.parametrize(
    ("text", "said"),
    [
        (_file(PROJECTS, head="tenants: a\n"), "scenario s: tenants: lists the tenants' values"),
        (_file(PROJECTS, head="tenants: []\n"), "scenario s: tenants: lists the tenants' values"),
        (
            _file(PROJECTS, head="tenants: [{a: 1}]\n"),
            "scenario s: tenants: lists the tenants' values",
        ),
        (_file(PROJECTS, head=f"tenants: [{A}, {A}]\n"), f"scenario s: tenants: lists {A} twice"),
        (
            _file(PROJECTS + "    crosses_tenants: fk_x\n"),
            "scenario s: app.tb_project: crosses_tenants lists column names",
        ),
    ],
)
def test_malformed_tenants_are_refused(tmp_path: Path, text: str, said: str) -> None:
    manager = _load(tmp_path, text)
    with pytest.raises(ScenarioError, match=re.escape(said)):
        manager.load(tmp_path / "s.yaml")


# ── spread: which tables are scoped, and each row's tenant ──────────────────────────


def _plans(**counts: int) -> dict[str, TablePlan]:
    return {f"app.tb_{name}": TablePlan(count) for name, count in counts.items()}


def _spread(  # noqa: PLR0913 — the tables, then each part of the run a test varies
    tables: dict[str, TablePlan],
    *,
    count: int = 2,
    values: tuple[object, ...] | None = (A, B),
    table: str | None = None,
    existing: frozenset[str] = frozenset(),
    rule: TenancyRule = RULE,
) -> Spread:
    order = [ref.display for ref in FACTS.insert_order({t: p.count for t, p in tables.items()})]
    return spread(FACTS, rule, tables, order, Tenants(count, values, table), existing=existing)


def test_rows_go_round_robin_over_the_tenants() -> None:
    found = _spread(_plans(project=5))
    assert (found.rows["app.tb_project"], found.columns) == (
        (0, 1, 0, 1, 0),
        {"app.tb_project": "tenant_id"},
    )


def test_a_child_without_the_column_is_scoped_through_its_key() -> None:
    found = _spread(_plans(project=2, task=4))
    assert (found.rows["app.tb_task"], found.keys["app.tb_task"]) == (
        (0, 1, 0, 1),
        frozenset({"fk_project"}),
    )


def test_a_shared_table_is_not_scoped() -> None:
    found = _spread(_plans(category=3, contact=4, organization=2), table="app.tb_organization")
    assert ("app.tb_category" in found.rows, found.keys["app.tb_contact"]) == (
        False,
        frozenset({"fk_organization"}),
    )


def test_the_tenant_tables_row_is_its_own_tenant() -> None:
    found = _spread(
        {
            "app.tb_organization": TablePlan(
                3, hierarchy=Hierarchy("fk_parent_organization", roots=1, fan_out=2)
            )
        },
        count=3,
        values=None,
        table="app.tb_organization",
    )
    assert (found.rows["app.tb_organization"], found.keys["app.tb_organization"]) == (
        (0, 1, 2),
        frozenset(),
    )


def test_a_tree_stays_in_its_roots_tenant() -> None:
    folders = TablePlan(8, hierarchy=Hierarchy("fk_parent_folder", roots=2, fan_out=3))
    assert _spread({"app.tb_folder": folders}).rows["app.tb_folder"] == (0, 1, 0, 0, 0, 1, 1, 1)


def test_an_excluded_table_is_not_scoped_even_through_a_key() -> None:
    tables = {
        **_plans(organization=2, category=1, contact=2),
        "app.tb_audit": TablePlan(2, fill=frozenset({"tenant_id"})),
    }
    assert "app.tb_audit" not in _spread(tables, table="app.tb_organization").rows


def test_a_crossing_key_goes_over_every_tenant() -> None:
    tables = {
        **_plans(organization=2, category=1, contact=2, project=2),
        "app.tb_task": TablePlan(4, crosses=frozenset({"fk_assignee"})),
    }
    found = _spread(tables, table="app.tb_organization")
    assert found.keys["app.tb_task"] == frozenset({"fk_project"})


@pytest.mark.parametrize(
    ("tables", "options", "said"),
    [
        (
            {**_plans(project=1), "app.tb_task": TablePlan(2)},
            {},
            "app.tb_task.fk_project points at app.tb_project's rows of its own tenant, and "
            f"app.tb_project has none of tenant {B}",
        ),
        (
            {
                "app.tb_folder": TablePlan(
                    3, hierarchy=Hierarchy("fk_parent_folder", roots=1, fan_out=2)
                ),
                "app.tb_project": TablePlan(2),
            },
            {"values": None},
            "",
        ),
        (
            {"app.tb_project": TablePlan(2, overrides={"tenant_id": A})},
            {},
            "app.tb_project.tenant_id is the tenant column, which the run's tenants fill: "
            "it cannot be overridden",
        ),
        (
            {"app.tb_project": TablePlan(2, trusted=frozenset({"tenant_id"}))},
            {},
            "app.tb_project.tenant_id is the tenant column, which the run's tenants fill: "
            "it cannot be trusted to a trigger",
        ),
        (
            {**_plans(project=2), "app.tb_task": TablePlan(2, crosses=frozenset({"title"}))},
            {},
            "app.tb_task crosses tenants by title, which is no key to a tenant-scoped table",
        ),
        (
            {"app.tb_project": TablePlan(2)},
            {"values": ("not-a-uuid", B)},
            "tenant 'not-a-uuid' is no uuid, which app.tb_project.tenant_id holds",
        ),
        (
            {"app.tb_task": TablePlan(2)},
            {"existing": frozenset({"app.tb_project"})},
            "app.tb_task.fk_project points at app.tb_project, under existing:, whose rows' "
            "tenants semis does not read",
        ),
    ],
    ids=[
        "parent-missing-in-a-tenant",
        "tree-roots-fewer-than-tenants-is-fine",
        "tenant-column-overridden",
        "tenant-column-trusted",
        "crossing-a-non-key",
        "tenant-not-a-uuid",
        "existing-scoped-parent",
    ],
)
def test_what_the_spread_refuses(
    tables: dict[str, TablePlan], options: dict[str, object], said: str
) -> None:
    if not said:
        _spread(tables, **options)  # ty: ignore[invalid-argument-type]
        return
    with pytest.raises(SemisError, match=re.escape(said)):
        _spread(tables, **options)  # ty: ignore[invalid-argument-type]


# ── the walk: each row written its tenant, each key within it ───────────────────────


def _walk(
    tables: dict[str, TablePlan], tenants: Tenants, *, rule: TenancyRule = RULE
) -> dict[str, list[Row]]:
    resolver = PrepSeedResolver()
    generator = FakeDataGenerator(FACTS, scenario_id=0x5010, seed=42)
    run: dict[str, list[Row]] = {}
    for table, stream in generator.walk(tables, resolver=resolver, tenancy=(rule, tenants)):
        rows = list(stream)
        resolver.remember(table, rows)
        run[table.ref.display] = run.get(table.ref.display, []) + rows
    return run


def test_each_row_holds_its_tenant_and_its_parent_is_of_it() -> None:
    run = _walk(_plans(project=3, task=6), Tenants(2, (A, B)))
    projects = {row["id"]: row["tenant_id"] for row in run["app.tb_project"]}
    assert [row["tenant_id"] for row in run["app.tb_project"]] == [A, B, A]
    assert [projects[row["fk_project"]] for row in run["app.tb_task"]] == [A, B] * 3


def test_children_go_round_robin_within_their_tenant() -> None:
    run = _walk(_plans(project=4, task=8), Tenants(2, (A, B)))
    ids = [row["id"] for row in run["app.tb_project"]]
    # Tenant A holds projects 0 and 2, B holds 1 and 3; tasks alternate A, B.
    assert [ids.index(row["fk_project"]) for row in run["app.tb_task"]] == [0, 1, 2, 3] * 2


def test_the_tenant_table_s_ids_are_the_tenants() -> None:
    organizations = TablePlan(2, overrides={"fk_parent_organization": None})
    tables = {"app.tb_organization": organizations, **_plans(category=1, contact=4)}
    run = _walk(tables, Tenants(2, table="app.tb_organization"))
    ids = [row["id"] for row in run["app.tb_organization"]]
    contacts = run["app.tb_contact"]
    assert [row["tenant_id"] for row in contacts] == ids * 2
    assert [row["fk_organization"] for row in contacts] == ids * 2


def test_without_tenants_a_run_is_unchanged() -> None:
    tables = _plans(project=3, task=6)
    resolver = PrepSeedResolver()
    plain: dict[str, list[Row]] = {}
    for table, stream in FakeDataGenerator(FACTS, scenario_id=0x5010, seed=42).walk(
        tables, resolver=resolver
    ):
        rows = list(stream)
        resolver.remember(table, rows)
        plain[table.ref.display] = rows
    spread_run = _walk(tables, Tenants(1, (A,)))
    assert [row["fk_project"] for row in plain["app.tb_task"]] == [
        row["fk_project"] for row in spread_run["app.tb_task"]
    ]


def test_a_copy_follows_the_parent_in_the_tenant() -> None:
    tables = {
        "app.tb_organization": TablePlan(2, overrides={"fk_parent_organization": None}),
        **_plans(category=1),
        "app.tb_contact": TablePlan(4, copies={"tenant_label": Copied("fk_organization", "id")}),
    }
    run = _walk(tables, Tenants(2, table="app.tb_organization"))
    assert [row["tenant_label"] for row in run["app.tb_contact"]] == [
        row["tenant_id"] for row in run["app.tb_contact"]
    ]


# ── a scenario's tenants, as the manager finds them ─────────────────────────────────

ORGANIZATIONS = (
    "  - name: app.tb_organization\n    count: 2\n    overrides: {fk_parent_organization: null}\n"
)
CONTACTS = "  - name: app.tb_category\n    count: 1\n  - name: app.tb_contact\n    count: 4\n"


def _check(tmp_path: Path, text: str, *, rule: TenancyRule | None = RULE) -> tuple[str, ...]:
    manager = _load(tmp_path, text, rule=rule)
    return manager.check(manager.load(tmp_path / "s.yaml"), no_pin=True)


def test_a_run_says_what_it_spreads_over_its_tenants(tmp_path: Path) -> None:
    notices = _check(tmp_path, _file(ORGANIZATIONS + CONTACTS))
    assert "scenario s spreads app.tb_contact over 2 tenants of app.tb_organization" in notices


def test_literal_tenants_are_said_by_their_count(tmp_path: Path) -> None:
    notices = _check(tmp_path, _file(PROJECTS, head=f"tenants: [{A}, {B}]\n"))
    assert "scenario s spreads app.tb_project over 2 tenants" in notices


def test_no_tenants_says_nothing_of_them(tmp_path: Path) -> None:
    notices = _check(tmp_path, _file(PROJECTS + "    overrides: {tenant_id: " + A + "}\n"))
    assert not [notice for notice in notices if "tenants" in notice]


def test_a_nullable_tenant_column_is_not_reported_left_null(tmp_path: Path) -> None:
    body = ORGANIZATIONS + CONTACTS + "  - name: app.tb_audit\n    count: 1\n"
    rule = TenancyRule("tenant_id", table="app.tb_organization")
    notices = _check(tmp_path, _file(body), rule=rule)
    assert not [notice for notice in notices if "app.tb_audit leaves" in notice]


@pytest.mark.parametrize(
    ("text", "rule", "said"),
    [
        (
            _file(PROJECTS, head=f"tenants: [{A}]\n"),
            None,
            "scenario s declares tenants:, and semis.yaml names no tenancy: column",
        ),
        (
            _file(ORGANIZATIONS + CONTACTS, head=f"tenants: [{A}]\n"),
            RULE,
            "scenario s: its tenants are the rows it writes into app.tb_organization, the "
            "tenant table; it lists tenants: too",
        ),
        (
            _file(CONTACTS) + "existing:\n  - name: app.tb_organization\n",
            RULE,
            "scenario s: existing app.tb_organization is the tenant table: name its tenants "
            "under where:",
        ),
        (
            _file(CONTACTS)
            + "existing:\n  - name: app.tb_organization\n    where: {identifier: [a, a]}\n",
            RULE,
            "scenario s: existing app.tb_organization is the tenant table, and where: names a twice",
        ),
        (
            _file(
                "  - name: app.tb_organization\n    count: 0\n"
                "    overrides: {fk_parent_organization: null}\n" + PROJECTS
            ),
            RULE,
            "scenario s writes no rows into app.tb_organization, the tenant table",
        ),
        (
            _file(PROJECTS + "    providers: {tenant_id: uuid4}\n", head=f"tenants: [{A}, {B}]\n"),
            RULE,
            "scenario s: app.tb_project.tenant_id is the tenant column, which the run's "
            "tenants fill: it cannot be given a provider",
        ),
    ],
    ids=["no-rule", "both", "whole-table", "repeated", "no-rows", "provider"],
)
def test_what_a_scenario_s_tenants_refuse(
    tmp_path: Path, text: str, rule: TenancyRule | None, said: str
) -> None:
    with pytest.raises(ScenarioError, match=re.escape(said)):
        _check(tmp_path, text, rule=rule)


def test_a_prep_seed_run_writes_each_tenant_s_rows(tmp_path: Path) -> None:
    manager = _load(
        tmp_path,
        _file(
            PROJECTS + "  - name: app.tb_task\n    count: 4\n",
            head=f"tenants: [{A}, {B}]\n",
            mode="prep-seed",
        ),
    )
    run = manager.execute(manager.load(tmp_path / "s.yaml"), tmp_path / "out", no_pin=True)
    projects = run.seeds[0].path.read_text()
    assert (projects.count(A), projects.count(B)) == (2, 2)


# ── the edges of the spread ────────────────────────────────────────────────────────


def test_an_excluded_existing_table_is_pointed_at_freely() -> None:
    rule = TenancyRule("tenant_id", exclude=frozenset({"app.tb_project"}))
    found = _spread(
        {"app.tb_task": TablePlan(2)}, existing=frozenset({"app.tb_project"}), rule=rule
    )
    assert "app.tb_task" not in found.rows


def test_a_key_may_cross_into_an_existing_scoped_table() -> None:
    found = _spread(
        {"app.tb_task": TablePlan(2, crosses=frozenset({"fk_project"}))},
        existing=frozenset({"app.tb_project"}),
    )
    assert "app.tb_task" not in found.rows


def test_a_tenant_unknown_at_check_is_named_by_its_place() -> None:
    with pytest.raises(ResolutionError, match=re.escape("none of tenant #2 of 2")):
        _spread(
            _plans(project=1, task=2),
            values=None,
            table="app.tb_organization",
            existing=frozenset({"app.tb_organization"}),
        )


def test_a_parent_with_no_rows_leaves_the_key_to_the_run() -> None:
    found = _spread(_plans(contact=0, project=2, task=2))
    assert found.keys["app.tb_task"] == frozenset({"fk_project", "fk_assignee"})


def test_a_tenant_column_that_is_a_key_points_at_the_tenant() -> None:
    tables = {
        "app.tb_organization": TablePlan(2, overrides={"fk_parent_organization": None}),
        **_plans(category=1, contact=2, membership=4),
    }
    run = _walk(tables, Tenants(2, table="app.tb_organization"))
    contacts = {row["id"]: row["tenant_id"] for row in run["app.tb_contact"]}
    assert [row["tenant_id"] for row in run["app.tb_membership"]] == [
        contacts[row["fk_contact"]] for row in run["app.tb_membership"]
    ]


def test_a_tenant_column_keyed_to_another_table_is_refused() -> None:
    with pytest.raises(
        ScenarioError,
        match=re.escape(
            "app.tb_membership.tenant_id is the tenant column, and a foreign key to "
            "app.tb_organization, which is not the run's tenant table"
        ),
    ):
        _spread(
            _plans(category=1, organization=1, contact=2, membership=2),
            rule=TenancyRule("tenant_id"),
        )


def test_a_translation_row_holds_its_entity_s_tenant() -> None:
    table = TranslationTable.of(FACTS, "app.tb_project", "app.tl_project", TranslationRule())
    locales = (Locale("fr", "fr_FR"), Locale("en", "en_US"))
    tables = {"app.tb_project": TablePlan(3, translation=Translation(table, locales))}
    resolver = PrepSeedResolver()
    resolver.locales("app.tb_locale", {"fr": A, "en": B})
    generator = FakeDataGenerator(FACTS, scenario_id=0x5010, seed=42)
    run: dict[str, list[Row]] = {}
    walk = generator.walk(
        tables,
        resolver=resolver,
        existing=frozenset({"app.tb_locale"}),
        tenancy=(RULE, Tenants(2, (A, B))),
    )
    for facts, stream in walk:
        rows = list(stream)
        resolver.remember(facts, rows)
        run[facts.ref.display] = rows
    assert [row["tenant_id"] for row in run["app.tl_project"]] == [A, A, B, B, A, A]


def test_prep_seed_takes_an_existing_tenant_table_s_uuids(tmp_path: Path) -> None:
    organizations = [UUID(int=1), UUID(int=2)]
    generator = FakeDataGenerator(FACTS, scenario_id=0x5010, seed=42)
    written = emit.prep_seed(
        generator,
        _plans(category=1, contact=4),
        tmp_path,
        existing={"app.tb_organization": organizations},
        tenancy=(RULE, Tenants(2, table="app.tb_organization")),
    )
    contacts = written[-1].path.read_text()
    assert [contacts.count(str(uuid)) for uuid in organizations] == [4, 4]


def test_an_unscoped_entity_s_translations_are_not_spread() -> None:
    rule = TenancyRule("tenant_id", exclude=frozenset({"app.tb_project"}))
    found = spread(
        FACTS,
        rule,
        {"app.tb_project": TablePlan(2)},
        ["app.tb_project", "app.tl_project"],
        Tenants(2, (A, B)),
        translations={"app.tl_project": "app.tb_project"},
    )
    assert found.columns == {}
