"""The exit codes confiture's refusals carry, which ``semis`` exits with unchanged (D11)."""

import pytest
from confiture.platform import (
    ConfigurationError,
    ConfiturError,
    DependencyCycleError,
    NotInModelError,
    SchemaError,
    SeedError,
    dependency_order,
    parse_schema,
)


@pytest.mark.parametrize(
    ("error", "exit_code"),
    [
        (SeedError("x"), 5),
        (ConfigurationError("x"), 5),
        (SchemaError("x"), 4),
        (NotInModelError("x"), 4),
        (ConfiturError("x"), 1),
    ],
)
def test_each_refusal_carries_its_exit_code(error: ConfiturError, exit_code: int) -> None:
    assert error.exit_code == exit_code


def test_a_dependency_cycle_exits_4() -> None:
    model = parse_schema(
        "CREATE TABLE a (id INT PRIMARY KEY, b INT REFERENCES b);"
        "CREATE TABLE b (id INT PRIMARY KEY, a INT REFERENCES a);"
    )
    with pytest.raises(DependencyCycleError) as cycle:
        dependency_order(model)
    assert (cycle.value.error_code, cycle.value.exit_code) == ("SCHEMA_202", 4)


def test_str_carries_the_hint() -> None:
    assert str(SeedError("refused", resolution_hint="do this")) == "refused\nHint: do this"
