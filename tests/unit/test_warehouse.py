"""A warehouse's chain, end to end: a real semis.yaml and scenario.

``tests/warehouse/`` holds a synthetic schema and its staging twins, the project file
naming them, and the scenario. An item needs a product, a product a supplier, a category
and a unit; every nullable parent is left NULL.
"""

import re
from pathlib import Path

import pytest

from fraiseql_semis import Project, seeds
from fraiseql_semis.scenario import Run

WAREHOUSE = Path(__file__).parent.parent / "warehouse"
ITEM_KEYS = ("fk_account_id", "fk_order_id")

Rows = dict[str, list[dict[str, str | None]]]


def _execute(directory: Path, fmt: seeds.Format | None = None) -> Run:
    manager = Project.load(WAREHOUSE / "semis.yaml").manager()
    scenario = manager.load(WAREHOUSE / "scenarios" / "stock.yaml")
    return manager.execute(scenario, directory, format=fmt)


def _copied(path: Path) -> list[dict[str, str | None]]:
    """A COPY seed's rows, each column's text as written, ``\\N`` as ``None``."""
    header, *lines = path.read_text().splitlines()
    columns = header[header.index("(") + 1 : header.index(")")].split(", ")
    return [
        {
            column: None if value == r"\N" else value
            for column, value in zip(columns, line.split("\t"), strict=True)
        }
        for line in lines
        if line != r"\."
    ]


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> Run:
    return _execute(tmp_path_factory.mktemp("stock"))


@pytest.fixture(scope="module")
def rows(tmp_path_factory: pytest.TempPathFactory) -> Rows:
    """The same run written as COPY, which reads back as text: prep-seed is reproducible."""
    copied = _execute(tmp_path_factory.mktemp("copied"), "copy")
    return {seed.table.name: _copied(seed.path) for seed in copied.seeds}


def test_each_table_is_written_into_its_twin_parents_first(run: Run) -> None:
    assert [(seed.table.display, seed.rows) for seed in run.seeds] == [
        ("prep_seed.tb_category", 3),
        ("prep_seed.tb_unit", 2),
        ("prep_seed.tb_supplier", 4),
        ("prep_seed.tb_product", 10),
        ("prep_seed.tb_item", 50),
    ]


def test_an_item_points_at_a_product_and_leaves_its_nullable_parents_null(rows: Rows) -> None:
    products = {row["id"] for row in rows["tb_product"]}
    assert all(row["fk_product_id"] in products for row in rows["tb_item"])
    assert all(row[key] is None for row in rows["tb_item"] for key in ITEM_KEYS)
    assert all(row["deleted_at"] is None for row in rows["tb_item"])


def test_a_variant_hangs_from_a_base_product(rows: Rows) -> None:
    ids = [row["id"] for row in rows["tb_product"]]
    parents = [row["fk_base_product_id"] for row in rows["tb_product"]]
    assert parents == [None, None] + [ids[(k - 2) // 4] for k in range(2, 10)]


def test_an_id_reads_back_as_its_table_code(rows: Rows) -> None:
    assert all(str(row["id"]).startswith("00030101-5201-") for row in rows["tb_item"])


def test_the_columns_draw_from_the_enabled_libraries(rows: Rows) -> None:
    suppliers = rows["tb_supplier"]
    assert all(re.fullmatch(r"[A-Z]{2}", str(row["country_code"])) for row in suppliers)
    assert all(row["company_name"] for row in suppliers)
    assert all("@" in str(row["email_address"]) for row in suppliers)


def test_levels_1_to_3_find_nothing_in_the_seeds(run: Run) -> None:
    """What they report is the fixture's schema: it holds no resolver."""
    report = seeds.validate(run.seeds[0].path.parent, schema_dir=WAREHOUSE / "schema", max_level=3)
    written = {seed.path.name for seed in run.seeds}
    assert {Path(path).name for path in report.scanned_files} == written
    assert [v for v in report.violations if Path(v.file_path).name in written] == []
