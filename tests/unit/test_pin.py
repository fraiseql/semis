"""SchemaPin: a digest of the facts semis consumes, tagged with its source kind (§8)."""

from datetime import date
from typing import cast

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import IncomparablePinError, PinError
from fraiseql_semis.pin import SchemaPin, describe_changes, projection, verify
from fraiseql_semis.schema import CONFITURE_VERSION, SchemaFacts
from tests.ddl import CODES, TRINITY

TABLES = ("catalog.tb_continent", "catalog.tb_country")


def _pin(ddl: str) -> SchemaPin:
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes(CODES))
    return SchemaPin.of(facts, TABLES)


def test_index_does_not_move_the_pin() -> None:
    indexed = TRINITY + "CREATE INDEX ix_continent_name ON catalog.tb_continent (name);"
    assert _pin(indexed).digest == _pin(TRINITY).digest


def test_not_null_on_a_written_column_moves_the_pin() -> None:
    nullable = TRINITY.replace("name VARCHAR(50) NOT NULL", "name VARCHAR(50)")
    assert _pin(nullable).digest != _pin(TRINITY).digest


def test_projection_is_stable_within_a_source() -> None:
    first, second = _pin(TRINITY), _pin(TRINITY)
    assert (first.source, first.digest) == (second.source, second.digest)
    assert first.digest.startswith("sha256:")


def test_the_pin_is_tagged_with_its_source_kind() -> None:
    assert _pin(TRINITY).source == "ddl"


def test_the_order_tables_are_listed_in_does_not_move_the_pin() -> None:
    facts = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))
    reversed_tables = tuple(reversed(TABLES))
    assert SchemaPin.of(facts, reversed_tables).digest == SchemaPin.of(facts, TABLES).digest


def test_a_table_outside_the_scenario_does_not_move_the_pin() -> None:
    other = "CREATE TABLE catalog.tb_other (pk_other BIGINT PRIMARY KEY, label TEXT);"
    assert _pin(TRINITY + other).digest == _pin(TRINITY).digest


def test_a_new_check_on_a_written_column_moves_the_pin() -> None:
    checked = TRINITY.replace(
        "iso_code CHAR(2) NOT NULL", "iso_code CHAR(2) NOT NULL CHECK (iso_code ~ '^[A-Z]{2}$')"
    )
    assert _pin(checked).digest != _pin(TRINITY).digest


def test_the_pin_records_confiture_and_the_day() -> None:
    facts = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))
    pin = SchemaPin.of(facts, TABLES, taken=date(2026, 9, 23))
    assert (pin.confiture, pin.taken) == (CONFITURE_VERSION, date(2026, 9, 23))


MOVED = TRINITY.replace("name VARCHAR(50) NOT NULL", "name VARCHAR(50)")


def _facts(ddl: str) -> SchemaFacts:
    return SchemaFacts.from_source(ddl, table_codes=TableCodes(CODES))


def test_replay_against_a_moved_schema_is_refused() -> None:
    recorded, current = _pin(TRINITY), _pin(MOVED)
    with pytest.raises(PinError) as refused:
        verify(recorded, _facts(MOVED), TABLES, scenario="minimal_seed")
    assert (refused.value.recorded, refused.value.current) == (recorded.digest, current.digest)


def test_the_refusal_names_what_moved() -> None:
    with pytest.raises(PinError) as refused:
        verify(_pin(TRINITY), _facts(MOVED), TABLES, scenario="minimal_seed")
    change = "catalog.tb_continent.name: not_null true → false"
    assert refused.value.changes == (change,)
    assert f"What moved:\n  {change}" in str(refused.value)


def test_a_pin_of_another_source_kind_is_refused_as_incomparable() -> None:
    live = SchemaPin("live", _pin(TRINITY).digest, "1.18.0", date(2026, 9, 23))
    with pytest.raises(IncomparablePinError, match="pinned against a live schema") as refused:
        verify(live, _facts(TRINITY), TABLES, scenario="minimal_seed")
    assert "cannot be compared" in str(refused.value)
    assert refused.value.changes == ()


def test_a_matching_pin_lets_the_run_proceed_and_says_so() -> None:
    notice = verify(_pin(TRINITY), _facts(TRINITY), TABLES, scenario="minimal_seed")
    assert notice.startswith("scenario minimal_seed matches its schema pin (ddl sha256:")


def test_an_unpinned_scenario_proceeds_and_says_so() -> None:
    notice = verify(None, _facts(MOVED), TABLES, scenario="minimal_seed")
    assert notice == "scenario minimal_seed is unpinned: its schema is not checked"


def test_no_pin_skips_the_check_and_says_so() -> None:
    notice = verify(_pin(TRINITY), _facts(MOVED), TABLES, scenario="minimal_seed", no_pin=True)
    assert notice == "scenario minimal_seed: the schema pin is not checked for this run (no_pin)"


def _projected(ddl: str, *, twins: tuple[str, ...] = ()) -> list[dict[str, object]]:
    return projection(_facts(ddl), TABLES, twins)


def test_equal_projections_describe_no_change() -> None:
    assert describe_changes(_projected(TRINITY), _projected(TRINITY)) == ()


@pytest.mark.parametrize(
    ("moved", "change"),
    [
        (
            TRINITY.replace("name VARCHAR(50) NOT NULL", "name VARCHAR(50)"),
            "catalog.tb_continent.name: not_null true → false",
        ),
        (
            TRINITY.replace("iso_code CHAR(2) NOT NULL,", "iso_code CHAR(2) NOT NULL UNIQUE,"),
            "catalog.tb_country.iso_code: unique false → true",
        ),
        (
            TRINITY.replace("name VARCHAR(50) NOT NULL", "name VARCHAR(50) NOT NULL DEFAULT 'x'"),
            "catalog.tb_continent.name: default null → 'x'",
        ),
        (
            TRINITY.replace(
                "iso_code CHAR(2) NOT NULL,", "iso_code CHAR(2) NOT NULL CHECK (iso_code <> ''),"
            ),
            "catalog.tb_country.iso_code: checks [] → [\"iso_code <> ''\"]",
        ),
    ],
    ids=["not_null", "unique", "default", "checks"],
)
def test_a_changed_column_field_is_named_with_both_values(moved: str, change: str) -> None:
    assert describe_changes(_projected(TRINITY), _projected(moved)) == (change,)


def test_a_type_change_names_both_spellings() -> None:
    moved = TRINITY.replace("name VARCHAR(50) NOT NULL", "name VARCHAR(60) NOT NULL")
    assert describe_changes(_projected(TRINITY), _projected(moved)) == (
        "catalog.tb_continent.name: type_key varchar(50) → varchar(60)",
        "catalog.tb_continent.name: raw_sql_type VARCHAR(50) → VARCHAR(60)",
    )


def test_a_column_added_or_removed_is_named() -> None:
    wider = TRINITY.replace(
        "name VARCHAR(50) NOT NULL", "name VARCHAR(50) NOT NULL,\n    note TEXT"
    )
    assert describe_changes(_projected(TRINITY), _projected(wider)) == (
        "catalog.tb_continent.note: column added",
    )
    assert describe_changes(_projected(wider), _projected(TRINITY)) == (
        "catalog.tb_continent.note: column removed",
    )


def test_a_column_moved_within_its_table_is_named() -> None:
    recorded = _projected(TRINITY)
    current = [dict(entry) for entry in recorded]
    columns = cast("list[dict[str, object]]", current[0]["columns"])
    current[0]["columns"] = [*columns[1:], columns[0]]
    names = ", ".join(str(column["name"]) for column in columns)
    moved = ", ".join(str(column["name"]) for column in [*columns[1:], columns[0]])
    assert describe_changes(recorded, current) == (
        f"catalog.tb_continent: column order {names} → {moved}",
    )


def test_a_foreign_key_and_a_trinity_role_are_named() -> None:
    recorded = _projected(TRINITY)
    current = [dict(entry) for entry in recorded]
    current[1] = {**current[1], "natural_id": None}
    columns = [dict(column) for column in cast("list[dict[str, object]]", current[1]["columns"])]
    key = next(column for column in columns if column["name"] == "fk_continent")
    key["foreign_key"] = ["catalog.tb_other", "pk_other"]
    current[1]["columns"] = columns
    assert describe_changes(recorded, current) == (
        "catalog.tb_country: natural_id id → null",
        'catalog.tb_country.fk_continent: foreign_key ["catalog.tb_continent", "pk_continent"]'
        ' → ["catalog.tb_other", "pk_other"]',
    )


def test_a_table_added_or_removed_is_named() -> None:
    one = projection(_facts(TRINITY), TABLES[:1])
    assert describe_changes(one, _projected(TRINITY)) == ("catalog.tb_country: table added",)
    assert describe_changes(_projected(TRINITY), one) == ("catalog.tb_country: table removed",)


def test_a_staging_twin_is_described_as_a_table_is() -> None:
    twin = ("prep_seed.tb_country",)
    gone = TRINITY[: TRINITY.index("CREATE TABLE prep_seed.tb_country")]
    narrower = TRINITY.replace("fk_continent_id UUID,", "fk_continent_id UUID NOT NULL,")
    assert describe_changes(_projected(TRINITY, twins=twin), _projected(gone, twins=twin)) == (
        "prep_seed.tb_country: staging twin removed",
    )
    assert describe_changes(_projected(gone, twins=twin), _projected(TRINITY, twins=twin)) == (
        "prep_seed.tb_country: staging twin added",
    )
    assert describe_changes(_projected(TRINITY, twins=twin), _projected(narrower, twins=twin)) == (
        "prep_seed.tb_country.fk_continent_id: not_null false → true",
    )


def test_an_unchanged_schema_digests_as_0_1_0_did() -> None:
    """Keeping the facts changes what a pin keeps, never what it digests."""
    facts = _facts(TRINITY)
    pin = SchemaPin.of(facts, TABLES, twins=("prep_seed.tb_continent", "prep_seed.tb_country"))
    assert pin.digest == "sha256:0dd2ecbdc53e00d8a95ac04b43121dff4ebf4a72c0d01fc0a6bde96d5f36b7ca"


def test_an_existing_tables_keys_are_pinned_and_a_moved_key_is_named() -> None:
    existing = ("catalog.tb_continent",)
    before = projection(_facts(TRINITY), TABLES[1:], existing=existing)
    assert before[-1] == {
        "existing": "catalog.tb_continent",
        "surrogate_pk": "pk_continent",
        "natural_id": "id",
        "columns": [_column_of(TRINITY, "id"), _column_of(TRINITY, "identifier")],
    }
    moved = TRINITY.replace(
        "identifier TEXT NOT NULL UNIQUE,\n    name", "identifier TEXT,\n    name", 1
    )
    after = projection(_facts(moved), TABLES[1:], existing=existing)
    assert describe_changes(before, after) == (
        "catalog.tb_continent.identifier: not_null true → false",
        "catalog.tb_continent.identifier: unique true → false",
    )
    assert describe_changes(before, before[:-1]) == (
        "catalog.tb_continent: existing table removed",
    )


def _column_of(ddl: str, name: str) -> dict[str, object]:
    (entry,) = projection(_facts(ddl), TABLES[:1])
    return next(c for c in cast("list[dict[str, object]]", entry["columns"]) if c["name"] == name)
