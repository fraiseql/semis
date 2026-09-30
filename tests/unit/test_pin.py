"""SchemaPin: a digest of the facts semis consumes, tagged with its source kind (§8)."""

from datetime import date
from pathlib import Path

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import IncomparablePinError, PinError
from fraiseql_semis.pin import SchemaPin, verify
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


def test_the_refusal_names_what_diff_reports() -> None:
    with pytest.raises(PinError) as refused:
        verify(_pin(TRINITY), _facts(MOVED), TABLES, scenario="minimal_seed", snapshot=TRINITY)
    change = "CHANGE COLUMN NULLABLE catalog.tb_continent.name FROM false TO true"
    assert refused.value.changes == (change,)
    assert f"diff reports:\n  {change}" in str(refused.value)


def test_without_a_snapshot_the_refusal_says_diff_has_nothing_to_read() -> None:
    with pytest.raises(PinError, match="kept no DDL snapshot"):
        verify(_pin(TRINITY), _facts(MOVED), TABLES, scenario="minimal_seed")


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


def test_a_snapshot_of_text_is_the_text() -> None:
    assert _facts(TRINITY).snapshot() == TRINITY


def test_a_snapshot_of_a_tree_reads_back_to_the_same_pin(tmp_path: Path) -> None:
    head, tail = TRINITY.split("CREATE TABLE catalog.tb_country")
    (tmp_path / "sub").mkdir()
    (tmp_path / "10_continent.sql").write_text(head)
    (tmp_path / "sub" / "20_country.sql").write_text("CREATE TABLE catalog.tb_country" + tail)
    (tmp_path / "notes.txt").write_text("not DDL")
    for source in (tmp_path, [tmp_path / "10_continent.sql", str(tmp_path / "sub")]):
        facts = SchemaFacts.from_source(source, table_codes=TableCodes(CODES))
        snapshot = facts.snapshot()
        assert snapshot is not None
        assert _pin(snapshot).digest == SchemaPin.of(facts, TABLES).digest


def test_facts_without_a_ddl_source_have_no_snapshot_and_no_diff() -> None:
    facts = SchemaFacts(_facts(TRINITY).model, TableCodes(CODES), source_kind="live")
    assert (facts.snapshot(), facts.changes_since(TRINITY)) == (None, None)


def test_a_digest_diff_cannot_explain_is_still_refused() -> None:
    stale = SchemaPin("ddl", "sha256:0", "1.18.0", date(2026, 9, 23))
    with pytest.raises(PinError, match="diff reports no change"):
        verify(stale, _facts(TRINITY), TABLES, scenario="minimal_seed", snapshot=TRINITY)


def test_no_pin_skips_the_check_and_says_so() -> None:
    notice = verify(_pin(TRINITY), _facts(MOVED), TABLES, scenario="minimal_seed", no_pin=True)
    assert notice == "scenario minimal_seed: the schema pin is not checked for this run (no_pin)"
