"""Connections a unit test hands semis in place of PostgreSQL's."""

from typing import Any


class Unreachable:
    """A connection that fails any test that uses it.

    It meets confiture's ``Connection`` protocol, so a test that only checks semis
    refuses before it connects passes one; a fake that answers statements extends it
    and overrides ``execute``, leaving the rest refused.
    """

    def cursor(self) -> Any:
        raise AssertionError("the connection was used")

    def execute(self, query: Any, params: Any = None) -> Any:
        raise AssertionError(f"the connection was used: {query}, {params}")

    def commit(self) -> None:
        raise AssertionError("the connection was used")

    def rollback(self) -> None:
        raise AssertionError("the connection was used")
