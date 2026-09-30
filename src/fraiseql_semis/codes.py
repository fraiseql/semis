"""The table-code registry: the one place a table name becomes a ``table_code``.

Codes are written in hex (D6) so a UUID's text reads back as the number registered:
``0x01020304`` is the ``01020304-…`` a row's ``id`` starts with.
"""

from collections.abc import Mapping

from fraiseql_semis.errors import CodeRegistryError

_CODE_LIMIT = 1 << 32  # a table code is the UUID's first four bytes


class TableCodes:
    """Qualified table name → the 32-bit code a semantic UUID starts with.

    Keys are schema-qualified (D14): ``catalog.tb_city`` and ``etl.tb_city`` are two
    tables and must be two codes. An unregistered table is refused, never hashed.
    """

    def __init__(self, mapping: Mapping[str, int]) -> None:
        tables_by_code: dict[int, str] = {}
        for table, code in mapping.items():
            if "." not in table:
                raise CodeRegistryError(
                    f"{table} is registered by a bare name",
                    resolution_hint="Register it schema-qualified, e.g. catalog.tb_city.",
                )
            if not 0 <= code < _CODE_LIMIT:
                raise CodeRegistryError(
                    f"{table}'s table code {code:#x} does not fit in 32 bits",
                    resolution_hint="Choose a code from 0x0 to 0xffffffff.",
                )
            if code in tables_by_code:
                raise CodeRegistryError(
                    f"{tables_by_code[code]} and {table} share table code {code:#x}",
                    resolution_hint="Give each table its own code.",
                )
            tables_by_code[code] = table
        self._codes = dict(mapping)
        self._tables = tables_by_code

    def code_for(self, table: str) -> int:
        """*table*'s code. *table* is schema-qualified, as it was registered."""
        try:
            return self._codes[table]
        except KeyError:
            raise CodeRegistryError(
                f"{table} has no table code",
                resolution_hint="Register it schema-qualified, with a hex code of its own.",
            ) from None

    def table_for(self, code: int) -> str | None:
        """The table registered with *code*, or ``None`` when no table has it."""
        return self._tables.get(code)

    def tables(self) -> list[str]:
        """Every registered table, by name."""
        return sorted(self._codes)
