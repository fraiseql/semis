"""readback.learn's refusals, which it makes before it reaches the database."""

from typing import Any, cast, override

import psycopg
import pytest

from fraiseql_semis import readback
from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ResolutionError
from fraiseql_semis.schema import SchemaFacts
from tests.unit.fakes import Unreachable


def test_a_table_without_trinity_roles_is_refused_before_the_database() -> None:
    ddl = "CREATE SCHEMA catalog; CREATE TABLE catalog.tb_note (body TEXT);"
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes({"catalog.tb_note": 0x0A}))
    with pytest.raises(ResolutionError, match=r"catalog\.tb_note shows no surrogate key"):
        readback.learn(Unreachable(), facts.facts_for("catalog.tb_note"), ["a"])


def test_paths_without_a_natural_id_are_refused_before_the_database() -> None:
    ddl = "CREATE SCHEMA catalog; CREATE TABLE catalog.tb_note (body TEXT);"
    facts = SchemaFacts.from_source(ddl, table_codes=TableCodes({"catalog.tb_note": 0x0A}))
    with pytest.raises(ResolutionError, match=r"catalog\.tb_note shows no natural id"):
        readback.set_paths(Unreachable(), facts.facts_for("catalog.tb_note"), "path", {})


def test_an_existing_table_without_a_surrogate_key_is_refused_before_the_database() -> None:
    ddl = "CREATE SCHEMA catalog; CREATE TABLE catalog.tb_note (identifier TEXT);"
    keys = SchemaFacts.from_source(ddl, table_codes=TableCodes({})).keys_for("catalog.tb_note")
    with pytest.raises(ResolutionError, match=r"existing catalog\.tb_note shows no surrogate key"):
        readback.existing_keys(Unreachable(), keys, by="identifier", values=None)


def test_the_reset_quotes_every_name() -> None:
    """Each name is written as an identifier, never pasted: confiture refuses one that
    needs quotes, and semis quotes the rest all the same."""
    ddl = "CREATE SCHEMA catalog; CREATE TABLE catalog.tb_x (body TEXT);"
    (ref,) = SchemaFacts.from_source(ddl, table_codes=TableCodes({})).insert_order()
    connection = _Deleting()
    readback.delete_range(connection, ref, "id", ("lo", "hi"))
    assert connection.statements == ['DELETE FROM "catalog"."tb_x" WHERE "id" BETWEEN %s AND %s']


class _Deleting(Unreachable):
    """A connection that records each statement, as a delete of no row."""

    rowcount = 0

    def __init__(self) -> None:
        self.statements: list[str] = []

    @override
    def execute(self, query: Any, params: Any = None) -> Any:
        self.statements.append(query.as_string())
        return self


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


class _Selecting(Unreachable):
    """A connection that records each statement, and answers it with no row."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    @override
    def execute(self, query: Any, params: Any = None) -> Any:
        self.statements.append(query.as_string())
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return []


def test_an_existing_read_selects_only_the_columns_it_is_asked_for() -> None:
    """A table with no identifier and no uuid id reads its keys as 0.3.0 did: the
    surrogate key alone, so a column it lacks is never named."""
    ddl = "CREATE SCHEMA s; CREATE TABLE s.tb_unit (pk_unit BIGINT PRIMARY KEY, label TEXT);"
    keys = SchemaFacts.from_source(ddl, table_codes=TableCodes({})).keys_for("s.tb_unit")
    connection = _Selecting()
    with pytest.raises(ResolutionError, match=r"existing s\.tb_unit holds no rows"):
        readback.existing_rows(connection, keys, by=None, values=None, natural_id=None)
    assert connection.statements == ['SELECT "pk_unit" FROM "s"."tb_unit" ORDER BY "pk_unit"']


def test_rows_named_by_values_need_the_column_that_names_them() -> None:
    ddl = "CREATE SCHEMA s; CREATE TABLE s.tb_unit (pk_unit BIGINT PRIMARY KEY);"
    keys = SchemaFacts.from_source(ddl, table_codes=TableCodes({})).keys_for("s.tb_unit")
    with pytest.raises(ValueError, match="need the column that names them"):
        readback.existing_rows(Unreachable(), keys, by=None, values=["x"], natural_id=None)


class _Dropped(Unreachable):
    """A connection lost mid-read: psycopg's error carries no SQLSTATE."""

    @override
    def execute(self, query: Any, params: Any = None) -> Any:
        raise psycopg.OperationalError("server closed the connection unexpectedly")


def test_a_connection_lost_while_counting_a_view_is_not_the_views_refusal() -> None:
    facts = SchemaFacts.from_source(
        "CREATE SCHEMA s; CREATE TABLE s.tb_a (id INT); CREATE VIEW s.v_a AS SELECT id FROM s.tb_a;",
        table_codes=TableCodes({}),
    )
    with pytest.raises(psycopg.OperationalError, match="closed the connection"):
        readback.count_view(cast("Any", _Dropped()), facts.views()[0], None)
