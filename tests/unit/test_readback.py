"""readback.learn's refusals, which it makes before it reaches the database."""

from typing import Any

import pytest

from fraiseql_semis import readback
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ResolutionError
from fraiseql_semis.schema import SchemaFacts


class _Unreachable:
    """A connection that fails any test that uses it."""

    def cursor(self) -> Any:
        raise AssertionError("the connection was used")

    def execute(self, query: Any, params: Any = None) -> Any:
        raise AssertionError(f"the connection was used: {query}, {params}")

    def commit(self) -> None:
        raise AssertionError("the connection was used")

    def rollback(self) -> None:
        raise AssertionError("the connection was used")


def test_a_table_without_trinity_roles_is_refused_before_the_database() -> None:
    ddl = "CREATE SCHEMA catalog; CREATE TABLE catalog.tb_note (body TEXT);"
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes({"catalog.tb_note": 0x0A}))
    with pytest.raises(ResolutionError, match=r"catalog\.tb_note shows no surrogate key"):
        readback.learn(_Unreachable(), facts.facts_for("catalog.tb_note"), ["a"])


def test_paths_without_a_natural_id_are_refused_before_the_database() -> None:
    ddl = "CREATE SCHEMA catalog; CREATE TABLE catalog.tb_note (body TEXT);"
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes({"catalog.tb_note": 0x0A}))
    with pytest.raises(ResolutionError, match=r"catalog\.tb_note shows no natural id"):
        readback.set_paths(_Unreachable(), facts.facts_for("catalog.tb_note"), "path", {})


def test_an_existing_table_without_a_surrogate_key_is_refused_before_the_database() -> None:
    ddl = "CREATE SCHEMA catalog; CREATE TABLE catalog.tb_note (identifier TEXT);"
    keys = SchemaFacts.from_source(ddl, table_codes=TableCodes({})).keys_for("catalog.tb_note")
    with pytest.raises(ResolutionError, match=r"existing catalog\.tb_note shows no surrogate key"):
        readback.existing_keys(_Unreachable(), keys, by="identifier", values=None)


@pytest.mark.parametrize(
    ("ddl", "table"),
    [
        ('CREATE SCHEMA "order"; CREATE TABLE "order".tb_x (body TEXT);', '"order"."tb_x"'),
        ('CREATE SCHEMA "a.b"; CREATE TABLE "a.b".tb_x (body TEXT);', '"a.b"."tb_x"'),
        ('CREATE SCHEMA s; CREATE TABLE s."Tb" (body TEXT);', '"s"."Tb"'),
        (
            'CREATE SCHEMA sales; CREATE TABLE sales."Tb ""Big"" Order" (body TEXT);',
            '"sales"."Tb ""Big"" Order"',
        ),
    ],
)
def test_the_reset_quotes_every_name_and_cascades_nowhere(ddl: str, table: str) -> None:
    """A reserved word, a dot or a capital letter: each reads back as the table it names,
    and a table outside the run is never emptied behind the reader's back."""
    refs = SchemaFacts.from_source(ddl, table_codes=TableCodes({})).insert_order()
    assert readback.reset(refs) == f"TRUNCATE {table} RESTART IDENTITY"


def test_the_reset_lists_every_table_in_order() -> None:
    ddl = "CREATE SCHEMA s; CREATE TABLE s.a (x TEXT); CREATE TABLE s.b (x TEXT);"
    refs = SchemaFacts.from_source(ddl, table_codes=TableCodes({})).insert_order()
    assert readback.reset(refs) == 'TRUNCATE "s"."a", "s"."b" RESTART IDENTITY'


class _Held:
    """A connection that records what is done with it."""

    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def __enter__(self) -> _Held:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.events.append(("closed",))

    def execute(self, query: Any, params: Any = None) -> None:
        self.events.append(("execute", query.as_string(), params))

    def commit(self) -> None:
        self.events.append(("commit",))

    def rollback(self) -> None:
        self.events.append(("rollback",))


def _opening(monkeypatch: pytest.MonkeyPatch) -> list[_Held]:
    opened: list[_Held] = []

    def connect(_url: str) -> _Held:
        opened.append(_Held())
        return opened[-1]

    monkeypatch.setattr(readback.psycopg, "connect", connect)
    return opened


LOCKED = (
    "execute",
    "SELECT pg_advisory_xact_lock(%s, %s)",
    [readback.SEMIS_LOCK_CLASS, 0x5001],
)


def test_exclusive_holds_the_scenarios_lock_until_the_block_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = _opening(monkeypatch)
    with readback.exclusive("postgresql:///x", 0x5001):
        assert [held.events for held in opened] == [[LOCKED]]
    assert opened[0].events == [LOCKED, ("commit",), ("closed",)]


def test_exclusive_lets_go_when_the_block_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    opened = _opening(monkeypatch)
    with pytest.raises(RuntimeError), readback.exclusive("postgresql:///x", 0x5001):
        raise RuntimeError
    assert opened[0].events == [LOCKED, ("rollback",), ("closed",)]


def test_the_lock_class_is_an_int4() -> None:
    assert -(2**31) <= readback.SEMIS_LOCK_CLASS < 2**31
