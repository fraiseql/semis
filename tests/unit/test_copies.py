"""copies: a column holding a column of the row its foreign key points at."""

from pathlib import Path

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ScenarioError
from fraiseql_semis.generator import Copied, FakeDataGenerator, Row
from fraiseql_semis.resolution import PrepSeedResolver
from fraiseql_semis.scenario import ScenarioManager
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import COPIES, COPIES_CODES

FACTS = SchemaFacts.from_source(COPIES, table_codes=TableCodes(COPIES_CODES))
ORGANIZATIONS = """\
  - name: tenant.tb_organization
    count: 6
"""
FLAT = "    overrides: {fk_parent_organization: null}\n"


def _scenario(  # noqa: PLR0913 — each part of the file a test varies, by keyword
    copies: str,
    *,
    contact: str = "",
    organization: str | None = FLAT,
    tables: str = "",
    tail: str = "",
    mode: str = "read-back",
) -> str:
    """A scenario of contacts copying *copies*; *tail* follows the contact's entry."""
    return (
        f"scenario_id: 0x5005\nname: contacts\nmode: {mode}\nseed: 42\ntables:\n"
        f"{tables}{ORGANIZATIONS if organization is not None else ''}{organization or ''}"
        "  - name: tenant.tb_contact\n    count: 12\n"
        f"    overrides: {{fk_referrer: null{contact}}}\n"
        f"    copies: {{{copies}}}\n{tail}"
    )


def _check(tmp_path: Path, text: str) -> tuple[str, ...]:
    path = tmp_path / "contacts.yaml"
    path.write_text(text)
    manager = ScenarioManager(FACTS)
    return manager.check(manager.load(path), no_pin=True)


def test_a_copy_of_the_parents_id_checks(tmp_path: Path) -> None:
    text = _scenario(
        "tenant_id: fk_customer_org.id, backup_tenant_id: fk_backup_org.id",
        contact=", fk_backup_org: null",
    )
    assert _check(tmp_path, text)[1:] == (
        # backup_tenant_id is copied, so it is not one nobody names.
        "tenant.tb_organization leaves note, created_by, path NULL; fill: draws them",
        "tenant.tb_contact leaves label, fk_category NULL; fill: draws the values, a parent "
        "under tables: or existing: points the keys",
    )


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (
            _scenario("tenant_id: label.id"),
            r"tenant\.tb_contact\.tenant_id copies label\.id, and label is not a foreign "
            r"key of tenant\.tb_contact",
        ),
        (
            _scenario("tenant_id: fk_referrer.id"),
            r"tenant\.tb_contact\.tenant_id copies fk_referrer\.id, and fk_referrer "
            r"references tenant\.tb_contact itself",
        ),
        (
            _scenario(
                "tenant_id: fk_customer_org.id",
                organization=None,
                tail="existing:\n  - name: tenant.tb_organization\n",
            ),
            r"tenant\.tb_contact\.tenant_id copies fk_customer_org\.id, and "
            r"tenant\.tb_organization is under existing:, whose keys semis reads, not its "
            r"columns",
        ),
        (
            _scenario("backup_tenant_id: fk_category.id"),
            r"tenant\.tb_contact\.backup_tenant_id copies fk_category\.id, and "
            r"tenant\.tb_category is not a table the run writes",
        ),
    ],
    ids=["not-a-key", "self-reference", "existing-parent", "parent-not-written"],
)
def test_a_copy_through_a_key_the_run_cannot_follow_is_refused(
    tmp_path: Path, text: str, match: str
) -> None:
    with pytest.raises(ScenarioError, match=rf"^scenario contacts: {match}"):
        _check(tmp_path, text)


@pytest.mark.parametrize(
    ("facts", "tail", "reason"),
    [
        (FACTS, "    trusts_trigger: [fk_backup_org]\n", "is trusted to a trigger"),
        (
            SchemaFacts.from_source(
                COPIES.replace(
                    "fk_backup_org BIGINT REFERENCES", "fk_backup_org BIGINT DEFAULT 1 REFERENCES"
                ),
                table_codes=TableCodes(COPIES_CODES),
            ),
            "",
            "has a default, which PostgreSQL applies",
        ),
    ],
    ids=["trusted", "default"],
)
def test_a_copy_through_a_key_semis_leaves_out_is_refused(
    tmp_path: Path, facts: SchemaFacts, tail: str, reason: str
) -> None:
    """The row does not carry the key, so semis does not know which parent it points at."""
    path = tmp_path / "contacts.yaml"
    path.write_text(_scenario("backup_tenant_id: fk_backup_org.id", tail=tail))
    manager = ScenarioManager(facts)
    with pytest.raises(
        ScenarioError,
        match=rf"^scenario contacts: tenant\.tb_contact\.backup_tenant_id copies "
        rf"fk_backup_org\.id, and fk_backup_org {reason}",
    ):
        manager.check(manager.load(path), no_pin=True)


HIERARCHY = "    hierarchy: {parent: fk_parent_organization, roots: 6, path: path}\n"


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (
            _scenario("tenant_id: fk_customer_org.nope"),
            r"tenant\.tb_organization\.nope, which is not a column semis writes",
        ),
        (
            _scenario("tenant_id: fk_customer_org.created_by"),
            r"tenant\.tb_organization\.created_by, which the run leaves NULL",
        ),
        (
            _scenario(
                "tenant_id: fk_customer_org.created_by",
                organization=FLAT + "    trusts_trigger: [created_by]\n",
            ),
            r"tenant\.tb_organization\.created_by, which is trusted to a trigger",
        ),
        (
            _scenario("label: fk_customer_org.status"),
            r"tenant\.tb_organization\.status, which has a default, which PostgreSQL applies",
        ),
        (
            _scenario("label: fk_customer_org.path", organization=HIERARCHY),
            r"tenant\.tb_organization\.path, which is the hierarchy's path, which read-back "
            r"fills from the keys",
        ),
    ],
    ids=["not-a-column", "left-null", "trusted", "default", "path"],
)
def test_a_copy_of_a_value_semis_does_not_know_is_refused(
    tmp_path: Path, text: str, match: str
) -> None:
    with pytest.raises(
        ScenarioError, match=rf"^scenario contacts: tenant\.tb_contact\.\w+ copies {match}"
    ):
        _check(tmp_path, text)


def test_a_copy_between_two_types_is_refused(tmp_path: Path) -> None:
    with pytest.raises(
        ScenarioError,
        match=r"^scenario contacts: tenant\.tb_contact\.label is text, and "
        r"tenant\.tb_organization\.id is uuid: a copy is not cast",
    ):
        _check(tmp_path, _scenario("label: fk_customer_org.id"))


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (
            _scenario("tenant_id: fk_backup_org.id", contact=", fk_backup_org: null"),
            r"tenant\.tb_contact\.tenant_id is NOT NULL, and copies fk_backup_org, which "
            r"the run leaves NULL",
        ),
        (
            _scenario(
                "tenant_id: fk_category.id",
                tables="  - name: tenant.tb_category\n    count: 0\n",
            ),
            r"tenant\.tb_contact\.tenant_id is NOT NULL, and copies fk_category, which "
            r"the run leaves NULL",
        ),
    ],
    ids=["overridden-null", "parent-without-rows"],
)
def test_a_not_null_column_copying_a_key_left_null_is_refused(
    tmp_path: Path, text: str, match: str
) -> None:
    with pytest.raises(ScenarioError, match=rf"^scenario contacts: {match}"):
        _check(tmp_path, text)


@pytest.mark.parametrize(
    ("copies", "match"),
    [
        ("nope: fk_customer_org.id", r"nope is not a column semis writes"),
        ("id: fk_customer_org.id", r"id is the natural id, which carries the encoded UUID"),
        ("identifier: fk_customer_org.identifier", r"identifier is the slug"),
        (
            "fk_backup_org: fk_customer_org.pk_organization",
            r"fk_backup_org is a foreign key, whose value comes from the run's mode",
        ),
    ],
    ids=["not-a-column", "natural-id", "slug", "foreign-key"],
)
def test_a_column_semis_writes_itself_cannot_be_copied_into(
    tmp_path: Path, copies: str, match: str
) -> None:
    with pytest.raises(
        ScenarioError, match=rf"^scenario contacts: tenant\.tb_contact\.{match}.*cannot be copied"
    ):
        _check(tmp_path, _scenario(copies))


@pytest.mark.parametrize(
    ("entry", "how"),
    [
        ("    overrides: {label: x}\n", "overridden"),
        ("    providers: {label: i18n.country_code}\n", "given a provider"),
        ("    fill: [label]\n", "under fill:"),
        ("    trusts_trigger: [label]\n", "trusted to a trigger"),
    ],
    ids=["overridden", "provided", "fill", "trusted"],
)
def test_a_copied_column_named_another_way_too_is_refused(
    tmp_path: Path, entry: str, how: str
) -> None:
    text = (
        "scenario_id: 0x5005\nname: contacts\nmode: read-back\ntables:\n"
        "  - name: tenant.tb_contact\n    count: 1\n"
        f"    copies: {{label: fk_customer_org.name}}\n{entry}"
    )
    path = tmp_path / "contacts.yaml"
    path.write_text(text)
    with pytest.raises(
        ScenarioError,
        match=rf"^scenario contacts: tenant\.tb_contact: label is both copied and {how}",
    ):
        ScenarioManager(FACTS).load(path)


def _prep_seed_walk(copies: dict[str, Copied]) -> dict[str, list[Row]]:
    resolver = PrepSeedResolver()
    generator = FakeDataGenerator(FACTS, scenario_id=0x5005, seed=42)
    run: dict[str, list[Row]] = {}
    walk = generator.walk(
        {"tenant.tb_organization": 6, "tenant.tb_contact": 12},
        overrides={
            "tenant.tb_organization": {"fk_parent_organization": None},
            "tenant.tb_contact": {"fk_referrer": None, "fk_backup_org": None},
        },
        copies={"tenant.tb_contact": copies},
        resolver=resolver,
    )
    for table, stream in walk:
        rows = list(stream)
        resolver.remember(table, rows)
        run[table.ref.display] = rows
    return run


def test_each_child_copies_the_row_its_key_points_at() -> None:
    """Twelve contacts over six organizations, round-robin: each holds its own's id."""
    run = _prep_seed_walk(
        {
            "tenant_id": Copied("fk_customer_org", "id"),
            "backup_tenant_id": Copied("fk_backup_org", "id"),
        }
    )
    organizations = [row["id"] for row in run["tenant.tb_organization"]]
    contacts = run["tenant.tb_contact"]
    assert [row["tenant_id"] for row in contacts] == organizations * 2
    assert [row["fk_customer_org"] for row in contacts] == organizations * 2
    # A key left NULL copies NULL into a nullable column.
    assert {row["backup_tenant_id"] for row in contacts} == {None}


def test_a_copy_of_a_drawn_value_is_the_value_drawn() -> None:
    run = _prep_seed_walk({"label": Copied("fk_customer_org", "identifier")})
    slugs = [row["identifier"] for row in run["tenant.tb_organization"]]
    assert [row["label"] for row in run["tenant.tb_contact"]] == slugs * 2
