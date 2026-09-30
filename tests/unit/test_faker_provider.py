"""FakerProvider: a value that satisfies the column receiving it."""

import time as time_module
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

import pytest
from faker import Faker

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import RowContractError
from fraiseql_semis.faker_provider import CustomProviderRegistry, FakerProvider
from fraiseql_semis.schema import ColumnFacts, SchemaFacts
from tests.ddl import CONTRACT, CONTRACT_CODES

TABLE = SchemaFacts.from_source(CONTRACT, table_codes=TableCodes(CONTRACT_CODES)).facts_for(
    "catalog.tb_product"
)
COLUMNS = {column.name: column for column in TABLE.columns}
PRODUCT = "catalog.tb_product"


def _values(column: ColumnFacts, count: int = 50) -> list[object]:
    provider = FakerProvider(seed=7)
    return [provider.value_for(column, table=PRODUCT) for _ in range(count)]


def test_enum_column_receives_a_declared_value() -> None:
    values = _values(COLUMNS["kind"])
    assert set(values) <= {"draft", "live", "gone"}
    assert len(set(values)) > 1


def test_varchar_length_is_respected() -> None:
    assert all(len(str(value)) <= 50 for value in _values(COLUMNS["name"]))
    assert all(len(str(value)) <= 2 for value in _values(COLUMNS["code"]))


def _column(name: str, type_key: str) -> ColumnFacts:
    return ColumnFacts(
        name=name,
        type_key=type_key,
        raw_sql_type=type_key,
        not_null=True,
        default=None,
        unique=False,
    )


def _one(column: ColumnFacts, provider: FakerProvider | None = None) -> object:
    return (provider or FakerProvider(seed=7)).value_for(column, table=PRODUCT)


def _seeded() -> Faker:
    faker = Faker("en_US")
    faker.seed_instance(7)
    return faker


def test_name_match_picks_the_provider_the_name_says() -> None:
    assert _one(_column("email", "text")) == _seeded().email()


def test_pattern_match_finds_the_name_inside_a_longer_one() -> None:
    assert _one(_column("billing_email", "text")) == _seeded().email()


def test_longest_pattern_wins() -> None:
    assert _one(_column("company_name", "text")) == _seeded().company()


def test_name_match_applies_only_to_text() -> None:
    assert isinstance(_one(_column("name", "integer")), int)


@pytest.mark.parametrize(
    ("type_key", "kind"),
    [
        ("text", str),
        ("smallint", int),
        ("integer", int),
        ("bigint", int),
        ("numeric(5,2)", Decimal),
        ("double precision", float),
        ("boolean", bool),
        ("date", date),
        ("timestamp", datetime),
        ("time", time),
        ("uuid", UUID),
    ],
)
def test_type_fallback(type_key: str, kind: type) -> None:
    assert isinstance(_one(_column("x", type_key)), kind)


def test_numeric_respects_precision_and_scale() -> None:
    provider = FakerProvider(seed=7)
    values = [_one(_column("x", "numeric(5,2)"), provider) for _ in range(50)]
    assert all(isinstance(v, Decimal) and abs(v) < 1000 for v in values)
    assert all(isinstance(v, Decimal) and v == round(v, 2) for v in values)


def test_timestamptz_is_aware() -> None:
    value = _one(_column("x", "timestamptz"))
    assert isinstance(value, datetime)
    assert value.tzinfo is not None


def test_a_type_no_provider_covers_yields_none() -> None:
    assert _one(_column("x", "inet")) is None


def test_registry_beats_name_pattern_and_type() -> None:
    registry = CustomProviderRegistry()
    registry.register_column(PRODUCT, "email", lambda _faker, _facts: "column")
    registry.register_global("mail", lambda _faker, _facts: "global")
    provider = FakerProvider(seed=7, registry=registry)
    assert _one(_column("email", "text"), provider) == "column"
    assert _one(_column("billing_email", "text"), provider) == "global"
    assert _one(_column("mailbox_size", "integer"), provider) == "global"


def test_registry_is_keyed_by_the_qualified_table() -> None:
    registry = CustomProviderRegistry()
    registry.register_column("etl.tb_product", "email", lambda _faker, _facts: "etl")
    provider = FakerProvider(seed=7, registry=registry)
    assert _one(_column("email", "text"), provider) == _seeded().email()


def test_a_custom_provider_receives_faker_and_the_facts() -> None:
    registry = CustomProviderRegistry()
    registry.register_column(PRODUCT, "code", lambda faker, facts: (faker.random_int(), facts))
    assert _one(COLUMNS["code"], FakerProvider(seed=7, registry=registry)) == (
        _seeded().random_int(),
        COLUMNS["code"],
    )


def test_numeric_without_a_scale_is_whole() -> None:
    value = _one(_column("x", "numeric(5)"))
    assert isinstance(value, Decimal)
    assert value == value.to_integral_value()


def test_unique_column_does_not_repeat_within_a_run() -> None:
    provider = FakerProvider(seed=7)
    sku = COLUMNS["sku"]
    values = [provider.value_for(sku, table=PRODUCT) for _ in range(500)]
    assert len(set(values)) == 500


def test_a_unique_column_that_runs_out_of_values_is_refused() -> None:
    flag = ColumnFacts(
        name="flag",
        type_key="boolean",
        raw_sql_type="BOOLEAN",
        not_null=True,
        default=None,
        unique=True,
    )
    provider = FakerProvider(seed=7)
    provider.value_for(flag, table=PRODUCT)
    provider.value_for(flag, table=PRODUCT)
    with pytest.raises(RowContractError, match=r"catalog\.tb_product\.flag is unique"):
        provider.value_for(flag, table=PRODUCT)


def test_uniqueness_is_per_table() -> None:
    provider = FakerProvider(seed=7)
    sku = COLUMNS["sku"]
    first = [provider.value_for(sku, table=PRODUCT) for _ in range(2)]
    other = [provider.value_for(sku, table="etl.tb_product") for _ in range(2)]
    assert (len(set(first)), len(set(other))) == (2, 2)


def test_a_time_value_does_not_depend_on_when_it_is_drawn() -> None:
    """Faker's defaults end *now*, read to the second: a prep-seed run moved with the
    clock (D10). The second draw is made in the next second."""
    columns = [_column("at", type_key) for type_key in ("date", "timestamp", "timestamptz", "time")]

    def draw() -> list[object]:
        provider = FakerProvider(seed=7)
        return [_one(column, provider) for column in columns for _ in range(20)]

    first = draw()
    time_module.sleep(1.01 - time_module.time() % 1)
    assert draw() == first
