"""A CHECK read as a list of values, in every spelling confiture gives one.

fraiseql/semis#11. The spellings are those measured from DDL and from a live database at
confiture 1.32, 1.36 and 1.44.
"""

from decimal import Decimal

import pytest

from fraiseql_semis.checks import value_list
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.faker_provider import CustomProviderRegistry, FakerProvider
from fraiseql_semis.generator import FakeDataGenerator, Row
from fraiseql_semis.providers import organization
from fraiseql_semis.schema import ColumnFacts, SchemaFacts


@pytest.mark.parametrize(
    ("column", "expression", "values"),
    [
        ("role", "role IN ('manager', 'sales', 'support')", ("manager", "sales", "support")),
        (
            "role",
            "role = ANY(ARRAY[CAST('manager' AS text), CAST('sales' AS text)])",
            ("manager", "sales"),
        ),
        (
            "status",
            "CAST(status AS text) = ANY(ARRAY[CAST('open' AS text), CAST('closed' AS text)])",
            ("open", "closed"),
        ),
        (
            "status",
            "((status)::text = ANY ((ARRAY['open'::character varying, 'closed'::character varying])::text[]))",
            ("open", "closed"),
        ),
        (
            "status",
            "CAST(status AS text) = ANY(CAST(ARRAY[CAST('open' AS varchar), "
            "CAST('closed' AS varchar)] AS text[]))",
            ("open", "closed"),
        ),
        ("lvl", "lvl IN (1, 2, 3)", (1, 2, 3)),
        ("lvl", "lvl = ANY(ARRAY[1, 2, 3])", (1, 2, 3)),
        ("delta", "delta IN (-1, 0.5, 2.25)", (-1, Decimal("0.5"), Decimal("2.25"))),
        ("note", "note IN ('it''s', 'a \"b\"')", ("it's", 'a "b"')),
        ("Kind", "\"Kind\" IN ('a')", ("a",)),
        ("kind", "(kind IN ('a', 'b'))", ("a", "b")),
        ("kind", "kind IN (('a'), 'b')", ("a", "b")),
        ("kind", "KIND in ('a')", ("a",)),
        ("kind", "kind IN (CAST('a' AS varchar(5)), 'b'::public.label)", ("a", "b")),
    ],
)
def test_a_plain_list_is_read(column: str, expression: str, values: tuple[object, ...]) -> None:
    assert value_list(column, expression) == values


@pytest.mark.parametrize(
    ("column", "expression"),
    [
        ("rng", "rng BETWEEN 1 AND 5"),
        ("rng", "rng >= 1 AND rng <= 5"),
        ("role", "role NOT IN ('a', 'b')"),
        ("role", "role <> ALL(ARRAY['a'])"),
        ("role", "role IN ('a') OR role IS NULL"),
        ("role", "other IN ('a')"),
        ("role", "lower(role) IN ('a')"),
        ("role", "role IN (SELECT x FROM t)"),
        ("role", "role IN ('a', other)"),
        ("role", "role IN ()"),
        ("role", "role IN ('a'"),
        ("role", "role IN ('a') AND role IN ('b')"),
        ("role", "role IN (E'a')"),
        ("role", "role = 'a'"),
        ("role", "role = ANY(other)"),
        ("role", "role ~ '^[a-z]+$'"),
        ("role", "'a' = ANY(ARRAY[role])"),
        ("role", "CAST(role AS text IN ('a')"),
        ("role", "role IN ('unterminated)"),
        ("role", "(role IN ('a')"),
        ("role", "role IN (CAST('a' AS varchar(5"),
        ("role", "role IN ("),
        ("role", "role::1 IN ('a')"),
    ],
)
def test_anything_else_is_not_a_list(column: str, expression: str) -> None:
    assert value_list(column, expression) is None


ROLE = "app.tb_agent"
AGENTS = """CREATE SCHEMA app;
CREATE TABLE app.tb_agent (
  pk_agent bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id uuid NOT NULL UNIQUE,
  role text NOT NULL CHECK (role IN ('manager', 'sales', 'support')),
  status varchar(20) NOT NULL,
  level integer NOT NULL CHECK (level IN (1, 2)) CHECK (level = ANY (ARRAY[2, 1, 3])),
  email text NOT NULL CHECK (email IN ('a@example.invalid', 'b@example.invalid')),
  rank integer NOT NULL CHECK (rank BETWEEN 1 AND 5),
  CONSTRAINT tb_agent_status_check CHECK (status IN ('open', 'closed'))
);"""
FACTS = SchemaFacts.from_source(AGENTS, table_codes=TableCodes({ROLE: 0x0F}))


def _agents(count: int = 7, registry: CustomProviderRegistry | None = None) -> list[Row]:
    generator = FakeDataGenerator(FACTS, scenario_id=0x5001, seed=42, providers=registry)
    return list(generator.generate_rows(ROLE, count))


def test_a_column_under_a_value_list_is_drawn_from_it_round_robin() -> None:
    rows = _agents()
    assert [row["role"] for row in rows] == ["manager", "sales", "support"] * 2 + ["manager"]
    assert [row["status"] for row in rows] == ["open", "closed"] * 3 + ["open"]


def test_two_lists_on_one_column_draw_from_the_values_both_allow() -> None:
    assert [row["level"] for row in _agents(4)] == [1, 2, 1, 2]


def test_a_value_list_beats_a_library_rule_and_a_name() -> None:
    registry = CustomProviderRegistry()
    registry.register_library(organization.LIBRARY)
    assert [row["email"] for row in _agents(2, registry)] == [
        "a@example.invalid",
        "b@example.invalid",
    ]


def test_a_registered_provider_beats_a_value_list() -> None:
    registry = CustomProviderRegistry()
    registry.register_column(ROLE, "role", lambda _faker, _column: "chief")
    assert {row["role"] for row in _agents(3, registry)} == {"chief"}


def test_a_check_that_is_not_a_list_is_drawn_as_its_type_is() -> None:
    assert all(isinstance(row["rank"], int) for row in _agents(3))


def test_the_turn_carries_over_from_one_call_to_the_next() -> None:
    """A hierarchy draws its table a level at a time: the list keeps its turn."""
    provider = FakerProvider(seed=7)
    column = next(column for column in FACTS.facts_for(ROLE).columns if column.name == "role")
    first = [provider.drawer(column, table=ROLE)() for _ in range(2)]
    second = [provider.drawer(column, table=ROLE)() for _ in range(2)]
    assert first + second == ["manager", "sales", "support", "manager"]


def test_lists_that_share_no_value_are_drawn_as_the_type_is() -> None:
    column = ColumnFacts(
        name="level",
        type_key="integer",
        raw_sql_type="INTEGER",
        not_null=True,
        default=None,
        unique=False,
        checks=("level IN (1)", "level IN (2)"),
    )
    value = FakerProvider(seed=7).value_for(column, table=ROLE)
    assert isinstance(value, int)
    assert value not in {1, 2}
