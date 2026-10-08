"""A scenario's pin file: where it lives, and what is refused when it does not read."""

import json
from pathlib import Path

import pytest

from fraiseql_semis import pin_file
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ScenarioError
from fraiseql_semis.pin import SchemaPin
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import CODES, TRINITY

PIN = SchemaPin.of(
    SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES)), ["catalog.tb_continent"]
)


def test_the_pin_lives_beside_the_scenario_named_after_it(tmp_path: Path) -> None:
    assert pin_file.path_for(tmp_path / "x.yaml", "s") == tmp_path / "s.pin.json"


def test_a_name_that_would_leave_the_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError, match=r"^scenario \.\./s: its pin would be written outside"):
        pin_file.path_for(tmp_path / "x.yaml", "../s")


def test_a_pin_written_reads_back_as_itself(tmp_path: Path) -> None:
    path = tmp_path / "s.pin.json"
    pin_file.write(path, PIN)
    assert pin_file.read(path, scenario="s") == PIN
    assert path.read_text().endswith("}\n")


def test_no_file_is_no_pin(tmp_path: Path) -> None:
    assert pin_file.read(tmp_path / "s.pin.json", scenario="s") is None


def _document(**changed: object) -> str:
    return json.dumps(PIN.to_document() | changed)


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("{not json", "its pin file is malformed"),
        ("[]", "its pin file is malformed"),
        (_document(extra=1), "its pin file is malformed"),
        (_document(source="file"), "its pin file is malformed"),
        (_document(taken="yesterday"), "its pin file is malformed"),
        (_document(taken=20261008), "its pin file is malformed"),
        (_document(facts=[]), "its pin file does not hold the facts its digest was taken from"),
        ("[" * 100_000 + "]" * 100_000, "its pin file is malformed"),
    ],
    ids=["json", "list", "unknown-key", "source", "date", "date-type", "facts", "nested"],
)
def test_a_pin_file_that_does_not_read_is_refused(tmp_path: Path, text: str, match: str) -> None:
    path = tmp_path / "s.pin.json"
    path.write_text(text)
    with pytest.raises(ScenarioError, match=rf"^scenario s: {match}") as refused:
        pin_file.read(path, scenario="s")
    assert refused.value.resolution_hint == (
        "Re-take it with semis pin on the scenario: it writes the file whole."
    )
