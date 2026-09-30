"""The semantic UUID encoding: what identifies a row semis generated."""

from typing import NamedTuple
from uuid import UUID

# Bytes per field, in the order the UUID carries them.
_TABLE_CODE, _SCENARIO_ID, _VERSION, _SEQUENCE = 4, 2, 2, 8


class UUIDFields(NamedTuple):
    """The four fields a semantic UUID carries, in byte order."""

    table_code: int
    scenario_id: int
    version: int
    sequence: int


def _fitted(field: str, value: int, width: int) -> bytes:
    """*value* as *width* big-endian bytes, refused rather than truncated when it does not fit.

    Truncating a table code would give two tables the same UUIDs.
    """
    if not 0 <= value < 1 << (8 * width):
        raise ValueError(f"{field} {value:#x} does not fit in {8 * width} bits")
    return value.to_bytes(width, "big")


class SemanticUUIDGenerator:
    """Deterministic UUIDs: ``table_code ‖ scenario_id ‖ version ‖ sequence``."""

    def __init__(self, scenario_id: int, version: int = 1) -> None:
        self._prefix = _fitted("scenario_id", scenario_id, _SCENARIO_ID) + _fitted(
            "version", version, _VERSION
        )
        self.scenario_id = scenario_id
        self.version = version
        self._sequences: dict[int, int] = {}

    def generate(self, table_code: int, sequence: int | None = None) -> UUID:
        """The UUID for *table_code*'s next row, or for *sequence* when one is given."""
        if sequence is None:
            sequence = self._sequences.get(table_code, 0) + 1
            self._sequences[table_code] = sequence
        return UUID(
            bytes=_fitted("table_code", table_code, _TABLE_CODE)
            + self._prefix
            + _fitted("sequence", sequence, _SEQUENCE)
        )

    def previous(self, table_code: int) -> UUID:
        """The UUID most recently generated for *table_code* by its counter."""
        if table_code not in self._sequences:
            raise LookupError(f"no UUID has been generated for table code {table_code:#x}")
        return self.generate(table_code, sequence=self._sequences[table_code])

    @staticmethod
    def decode(value: UUID) -> UUIDFields:
        b = value.bytes
        return UUIDFields(
            table_code=int.from_bytes(b[0:4], "big"),
            scenario_id=int.from_bytes(b[4:6], "big"),
            version=int.from_bytes(b[6:8], "big"),
            sequence=int.from_bytes(b[8:16], "big"),
        )
