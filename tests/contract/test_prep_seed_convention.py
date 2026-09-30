"""The staging convention confiture's prep-seed level 1 checks, which the twin follows.

A prep-seed seed targets the staging schema, and names each foreign key ``<fk>_id``.
"""

from collections.abc import Mapping
from pathlib import Path

from confiture.platform import PrepSeedPattern, validate_seeds

from fraiseql_semis import ScenarioManager, SchemaFacts, TableCodes, seeds
from fraiseql_semis.scenario import Scenario, TableSpec
from tests.ddl import CODES, TRINITY

FACTS = SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES))
CONTINENT = "02030405-5001-0001-0000-000000000001"


def _level_1(
    tmp_path: Path, table: str, row: Mapping[str, object], facts: SchemaFacts = FACTS
) -> set[PrepSeedPattern]:
    seeds.write(tmp_path / "001.sql", table, [row], facts=facts, mode="prep-seed")
    report = validate_seeds(tmp_path, schema_dir=tmp_path, max_level=1)
    return {violation.pattern for violation in report.violations}


def test_a_seed_targeting_the_catalog_is_an_error(tmp_path: Path) -> None:
    row = {"id": CONTINENT, "identifier": "x", "name": "Europe"}
    assert PrepSeedPattern.PREP_SEED_TARGET_MISMATCH in _level_1(
        tmp_path, "catalog.tb_continent", row
    )


def test_a_foreign_key_without_its_id_suffix_is_a_warning(tmp_path: Path) -> None:
    """Read only in an INSERT into ``prep_seed.``, spelled so: the check names the schema."""
    unsuffixed = SchemaFacts.from_source(
        TRINITY.replace("fk_continent_id UUID", "fk_continent UUID"), table_codes=TableCodes(CODES)
    )
    row = {"id": CONTINENT, "identifier": "x", "fk_continent": CONTINENT, "iso_code": "FR"}
    assert PrepSeedPattern.INVALID_FK_NAMING in _level_1(
        tmp_path, "prep_seed.tb_country", row, unsuffixed
    )


def test_the_twin_meets_the_convention(tmp_path: Path) -> None:
    """Level 1 finds nothing on the twin: the slug is not read as a UUID (#387)."""
    scenario = Scenario(
        id=0x5001,
        name="twin",
        mode="prep-seed",
        tables=(TableSpec("catalog.tb_continent", 2), TableSpec("catalog.tb_country", 3)),
        seed=42,
    )
    run = ScenarioManager(FACTS).execute(scenario, tmp_path / "out")
    seeds_dir = tmp_path / "seeds"
    seeds_dir.mkdir()
    for seed in run.seeds:
        (seeds_dir / seed.path.name).write_text(seed.path.read_text())
    report = validate_seeds(seeds_dir, schema_dir=tmp_path, max_level=1)
    assert report.violations == []
