"""The semantic UUID encoding: what identifies a row semis generated.

A semantic UUID is an RFC 9562 version 8 UUID, whose 122 free bits carry four fields:
``table_code(32) ‖ scenario_id(16) ‖ 8 ‖ version(12) ‖ variant ‖ sequence(62)``. The
table code and the scenario id fill the first two groups of its text, so codes written
in hex read back as typed.
"""

from typing import NamedTuple
from uuid import RFC_4122, UUID

# Bits per field.
_TABLE_CODE, _SCENARIO_ID, _VERSION, _SEQUENCE = 32, 16, 12, 62

_V8 = 8
_UUID_VERSION = _V8 << 76  # the version nibble, above the 12-bit version field
_RFC_VARIANT = 0b10 << 62  # the variant bits, above the 62-bit sequence


class UUIDFields(NamedTuple):
    """The four fields a semantic UUID carries, in the order its text shows them."""

    table_code: int
    scenario_id: int
    version: int
    sequence: int


def _fitted(field: str, value: int, bits: int) -> int:
    """*value*, refused rather than truncated when it does not fit in *bits*.

    Truncating a table code would give two tables the same UUIDs.
    """
    if not 0 <= value < 1 << bits:
        raise ValueError(f"{field} {value:#x} does not fit in {bits} bits")
    return value


def scenario_bounds(table_code: int, scenario_id: int) -> tuple[UUID, UUID]:
    """The least and the greatest UUID semis encodes for *table_code* in *scenario_id*,
    of any version and sequence: every row of that table that scenario wrote lies
    between them, as PostgreSQL orders UUIDs, byte by byte."""
    prefix = (
        _fitted("table_code", table_code, _TABLE_CODE) << 96
        | _fitted("scenario_id", scenario_id, _SCENARIO_ID) << 80
        | _UUID_VERSION
        | _RFC_VARIANT
    )
    every = (1 << _VERSION) - 1 << 64 | (1 << _SEQUENCE) - 1
    return UUID(int=prefix), UUID(int=prefix | every)


class SemanticUUIDGenerator:
    """Deterministic UUIDs: ``table_code ‖ scenario_id ‖ version ‖ sequence``, version 8."""

    def __init__(self, scenario_id: int, version: int = 1) -> None:
        self._prefix = (
            _fitted("scenario_id", scenario_id, _SCENARIO_ID) << 80
            | _UUID_VERSION
            | _fitted("version", version, _VERSION) << 64
            | _RFC_VARIANT
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
            int=_fitted("table_code", table_code, _TABLE_CODE) << 96
            | self._prefix
            | _fitted("sequence", sequence, _SEQUENCE)
        )

    def previous(self, table_code: int) -> UUID:
        """The UUID most recently generated for *table_code* by its counter."""
        if table_code not in self._sequences:
            raise LookupError(f"no UUID has been generated for table code {table_code:#x}")
        return self.generate(table_code, sequence=self._sequences[table_code])

    @staticmethod
    def decode(value: UUID) -> UUIDFields:
        """The four fields *value* carries; a UUID other than version 8 is refused."""
        if value.variant == RFC_4122 and value.version == _V8:
            bits = value.int
            return UUIDFields(
                table_code=bits >> 96,
                scenario_id=bits >> 80 & 0xFFFF,
                version=bits >> 64 & 0xFFF,
                sequence=bits & (1 << _SEQUENCE) - 1,
            )
        kind = "not an RFC 9562" if value.version is None else f"a version {value.version}"
        raise ValueError(f"{value} is {kind} UUID, which semis did not encode")
