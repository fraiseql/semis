"""emit.prep_seed: a run in Mode A is files, and nothing else."""

from pathlib import Path

import psycopg
import pytest

from fraiseql_semis import emit
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.generator import FakeDataGenerator
from fraiseql_semis.hierarchy import Hierarchy
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import CODES, HIERARCHY, HIERARCHY_CODES, TRINITY

FACTS = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))
COUNTS = {"catalog.tb_country": 4, "catalog.tb_continent": 2}


def _prep_seed(out: Path) -> list[Path]:
    generator = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42)
    return [seed.path for seed in emit.prep_seed(generator, COUNTS, out)]


def _refuse(*_: object, **__: object) -> None:
    raise AssertionError("prep-seed mode opened a connection")


def test_prep_seed_opens_no_connection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(psycopg, "connect", _refuse)
    monkeypatch.setattr(psycopg.Connection, "connect", _refuse)
    paths = _prep_seed(tmp_path)
    assert [path.name for path in paths] == [
        "001_prep_seed.tb_continent.sql",
        "002_prep_seed.tb_country.sql",
    ]


def test_prep_seed_writes_inserts_carrying_the_parents_uuid(tmp_path: Path) -> None:
    continents, countries = (path.read_text() for path in _prep_seed(tmp_path))
    parent = "02030405-5001-8001-8000-000000000001"
    assert continents.startswith("INSERT INTO prep_seed.tb_continent")
    assert parent in continents
    assert countries.count(parent) == 2


def test_prep_seed_is_byte_reproducible(tmp_path: Path) -> None:
    first = [path.read_bytes() for path in _prep_seed(tmp_path / "a")]
    second = [path.read_bytes() for path in _prep_seed(tmp_path / "b")]
    assert first == second


def test_a_hierarchy_is_one_prep_seed_file_roots_first(tmp_path: Path) -> None:
    facts = SchemaFacts.from_source(HIERARCHY, table_codes=TableCodes(HIERARCHY_CODES))
    generator = FakeDataGenerator(facts, scenario_id=0x5001, seed=42)
    tree = Hierarchy(parent="fk_parent_location", roots=2, fan_out=3)
    (seed,) = emit.prep_seed(
        generator, {"catalog.tb_location": 8}, tmp_path, hierarchies={"catalog.tb_location": tree}
    )
    text = seed.path.read_text()
    first_root = "05060708-5001-8001-8000-000000000001"
    assert seed.path.name == "001_prep_seed.tb_location.sql"
    assert text.count(first_root) == 1 + 3  # its own row, then three children
    assert text.index(first_root) < text.index("05060708-5001-8001-8000-000000000008")
