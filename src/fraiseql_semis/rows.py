"""The row contract (ARCHITECTURE §6): is this row emittable.

Confiture's writers check a row's shape, not its completeness or its values; this is
where both are checked. Pure: facts and a row in, violations out.
"""

import re
from collections.abc import Container, Iterator, Mapping
from dataclasses import dataclass

from fraiseql_semis.errors import RowContractError
from fraiseql_semis.schema import ColumnFacts, TableFacts

# `type_key` spells a declared length the same from DDL and from a live database;
# `raw_sql_type` says `bpchar(2)` for `CHAR(2)`.
_LENGTH = re.compile(r"(?:varchar|char)\((\d+)\)")


@dataclass(frozen=True)
class Violation:
    """One column of one row failing one fact."""

    table: str
    column: str
    fact: str
    found: str

    def __str__(self) -> str:
        return f"{self.table}.{self.column} is {self.fact}, but {self.found}"


def declared_length(column: ColumnFacts) -> int | None:
    """The most characters *column* holds, when its type declares a length."""
    match = _LENGTH.fullmatch(column.type_key or "")
    return int(match.group(1)) if match else None


def check_row(
    table: TableFacts,
    row: Mapping[str, object],
    *,
    trusted: frozenset[str] = frozenset(),
    seen: Mapping[str, Container[object]] | None = None,
) -> list[Violation]:
    """Every way *row* fails *table*'s contract; empty when it may be emitted.

    *trusted* names the columns a trigger fills (``trusts_trigger``): their absence is
    not refused. *seen* holds, per ``unique`` column, the values earlier rows of the run
    used.
    """
    if isinstance(trusted, str):
        # A bare string would match by substring: `"id" in "identifier"`.
        raise TypeError("trusted is a set of column names, not one string")
    return [
        Violation(table.ref.display, column.name, fact, found)
        for column in table.columns
        for fact, found in _failures(
            column, row, column.name in trusted, (seen or {}).get(column.name, ())
        )
    ]


def require_row(
    table: TableFacts,
    row: Mapping[str, object],
    *,
    trusted: frozenset[str] = frozenset(),
    seen: Mapping[str, Container[object]] | None = None,
) -> None:
    """Refuse *row* with ``RowContractError`` when ``check_row`` finds any violation."""
    violations = check_row(table, row, trusted=trusted, seen=seen)
    if violations:
        raise RowContractError(
            "\n".join(str(violation) for violation in violations),
            resolution_hint=(
                "Give the column a value that satisfies it, or a default in the schema; "
                "if a trigger fills it, name it under trusts_trigger for this table."
            ),
        )


def _failures(
    column: ColumnFacts, row: Mapping[str, object], trusted: bool, seen: Container[object]
) -> Iterator[tuple[str, str]]:
    value = row.get(column.name)
    if column.name not in row:
        if column.not_null and column.default is None and not trusted:
            yield "NOT NULL with no default", "the row does not carry it"
        return
    if value is None:
        # An explicit NULL is written as NULL: a default applies only to an omitted column.
        if column.not_null:
            yield "NOT NULL", "its value is None"
        return
    if column.enum_values is not None and value not in column.enum_values:
        yield f"an enum of ({', '.join(column.enum_values)})", f"{value!r} is not one of them"
    length = declared_length(column)
    if length is not None and len(str(value)) > length:
        yield column.type_key or "", f"{value!r} has {len(str(value))} characters"
    if column.unique and value in seen:
        yield "unique", f"{value!r} was already used in this run"
