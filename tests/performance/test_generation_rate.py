"""How fast semis draws rows, and what drawing them holds: nothing written or applied.

The rate's table is the warehouse's item, from ``tests/warehouse/``: wide, matched by the
shipped libraries, and keyed to its parents, which is what a real scenario's rows cost. The walk resolves each foreign key as prep-seed does.

A tracer, coverage's included, halves the rate it times, so the rate runs untraced:
``uv run pytest tests/performance --no-cov``, as CI does. Memory is not timed, and runs
with the suite. ``SEMIS_RATE_FLOOR`` lowers the floor on a slower machine: a hosted CI
runner draws under half the rate the floor was set on, and CI asks for 5,000.
"""

import os
import sys
import time
import tracemalloc
from dataclasses import replace
from pathlib import Path

import pytest

from fraiseql_semis import Project
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.faker_provider import CustomProviderRegistry
from fraiseql_semis.generator import FakeDataGenerator
from fraiseql_semis.resolution import PrepSeedResolver
from fraiseql_semis.schema import SchemaFacts

WAREHOUSE = Path(__file__).parent.parent / "warehouse"
ITEMS = 10_000
FLOOR = 10_000  # rows per second: docs/PRD.md, "Generate 10,000 rows/second minimum"
MEASURED = (
    21_500  # rows per second on 2026-09-30, the warehouse fixture, best of three, Python 3.13
)


def _floor() -> int:
    return int(os.environ.get("SEMIS_RATE_FLOOR", FLOOR))


def _traced() -> bool:
    monitoring = getattr(sys, "monitoring", None)
    return sys.gettrace() is not None or (
        monitoring is not None and monitoring.get_tool(monitoring.COVERAGE_ID) is not None
    )


untraced = pytest.mark.skipif(
    _traced(), reason="a tracer times itself: run tests/performance with --no-cov"
)


def _rows_per_second() -> float:
    project = Project.load(WAREHOUSE / "semis.yaml")
    scenario = project.manager().load(WAREHOUSE / "scenarios" / "stock.yaml")
    tables = tuple(
        replace(spec, count=ITEMS) if spec.name == "inventory.tb_item" else spec
        for spec in scenario.tables
    )
    named = {name: p for library in project.libraries for name, p in library.named().items()}
    registry = CustomProviderRegistry()
    for library in project.libraries:
        registry.register_library(library)
    for spec in tables:
        for column, name in spec.providers.items():
            registry.register_column(spec.name, column, named[name])
    generator = FakeDataGenerator(
        project.facts(), scenario.id, seed=scenario.seed, locale=scenario.locale, providers=registry
    )
    resolver = PrepSeedResolver()
    walk = generator.walk(
        {spec.name: spec.count for spec in tables},
        overrides={spec.name: spec.overrides for spec in tables},
        hierarchies={spec.name: spec.hierarchy for spec in tables if spec.hierarchy is not None},
        resolver=resolver,
    )
    drawn = 0
    started = time.perf_counter()
    for table, stream in walk:
        rows = list(stream)
        resolver.remember(table, rows)
        drawn += len(rows)
    return drawn / (time.perf_counter() - started)


def test_the_floor_is_the_prds_unless_the_environment_lowers_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEMIS_RATE_FLOOR", raising=False)
    prds = _floor()
    monkeypatch.setenv("SEMIS_RATE_FLOOR", "5000")
    assert (prds, _floor()) == (10_000, 5_000)


@untraced
def test_ten_thousand_rows_per_second() -> None:
    rate, floor = _rows_per_second(), _floor()
    assert rate >= floor, (
        f"{rate:,.0f} rows/s drawn, below the floor of {floor:,}; "
        f"{rate / MEASURED:.0%} of the {MEASURED:,} measured when the floor was set"
    )


# No unique column: a unique column's values are remembered for the run, by design, and
# this measures what the rows themselves hold.
READINGS = SchemaFacts.from_source(
    """
    CREATE SCHEMA metering;
    CREATE TABLE metering.tb_reading (
        pk_reading BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        id UUID NOT NULL,
        label TEXT NOT NULL,
        note TEXT,
        taken_at TIMESTAMPTZ NOT NULL,
        value INTEGER NOT NULL
    );
    """,
    table_codes=TableCodes({"metering.tb_reading": 0x06070809}),
)


def _peak_while_drawing(count: int) -> int:
    """The most memory held at once while *count* rows are drawn and dropped."""
    generator = FakeDataGenerator(READINGS, scenario_id=0x5001, seed=42)
    tracemalloc.start()
    try:
        for _row in generator.generate_rows("metering.tb_reading", count):
            pass
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_row_memory_does_not_grow_with_count() -> None:
    few, many = _peak_while_drawing(1_000), _peak_while_drawing(10_000)
    assert many < 2 * few, (
        f"drawing 10,000 rows peaked at {many:,} bytes, 1,000 at {few:,}: "
        "the rows are held rather than streamed"
    )
