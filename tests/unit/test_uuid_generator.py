"""The semantic UUID: an RFC 9562 version 8 UUID carrying table code, scenario, version and
sequence in 32, 16, 12 and 62 bits."""

from collections.abc import Callable
from uuid import RFC_4122, UUID

import pytest

from fraiseql_semis.uuid_generator import SemanticUUIDGenerator, scenario_bounds


def test_round_trip_encodes_four_fields() -> None:
    generator = SemanticUUIDGenerator(scenario_id=0x5001, version=1)
    encoded = generator.generate(0x01020304, sequence=66)
    assert SemanticUUIDGenerator.decode(encoded) == (0x01020304, 0x5001, 1, 66)


@pytest.mark.parametrize(
    "fields",
    [
        (0, 0, 0, 0),
        (2**32 - 1, 2**16 - 1, 2**12 - 1, 2**62 - 1),
    ],
)
def test_round_trip_is_exact_at_every_width(fields: tuple[int, int, int, int]) -> None:
    table_code, scenario_id, version, sequence = fields
    generator = SemanticUUIDGenerator(scenario_id=scenario_id, version=version)
    assert SemanticUUIDGenerator.decode(generator.generate(table_code, sequence=sequence)) == fields


@pytest.mark.parametrize(
    ("field", "build"),
    [
        ("table_code", lambda: SemanticUUIDGenerator(1).generate(2**32, sequence=1)),
        ("table_code", lambda: SemanticUUIDGenerator(1).generate(-1, sequence=1)),
        ("scenario_id", lambda: SemanticUUIDGenerator(2**16)),
        ("version", lambda: SemanticUUIDGenerator(1, version=2**12)),
        ("sequence", lambda: SemanticUUIDGenerator(1).generate(1, sequence=2**62)),
    ],
)
def test_refuses_a_field_too_wide(field: str, build: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match=field):
        build()


def test_a_semantic_uuid_is_an_rfc_9562_version_8_uuid() -> None:
    encoded = SemanticUUIDGenerator(scenario_id=0x5001).generate(0x02030405, sequence=0x42)
    assert (encoded.variant, encoded.version) == (RFC_4122, 8)


def test_the_codes_read_back_in_the_uuid_text() -> None:
    encoded = SemanticUUIDGenerator(scenario_id=0x5001).generate(0x02030405, sequence=0x42)
    assert str(encoded) == "02030405-5001-8001-8000-000000000042"


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("550e8400-e29b-41d4-a716-446655440000", "a version 4"),
        ("01890a5d-ac96-774b-bcce-b302099a8057", "a version 7"),
        ("02030405-5001-0001-0000-000000000042", "not an RFC 9562"),  # NCS's variant
        ("02030405-5001-0001-c000-000000000042", "not an RFC 9562"),  # Microsoft's variant
    ],
)
def test_a_uuid_semis_did_not_encode_is_refused(value: str, kind: str) -> None:
    with pytest.raises(ValueError, match=f"^{value} is {kind} UUID, which semis did not encode$"):
        SemanticUUIDGenerator.decode(UUID(value))


def test_sequence_counts_per_table_code() -> None:
    generator = SemanticUUIDGenerator(scenario_id=0x5001)
    sequences = [
        SemanticUUIDGenerator.decode(generator.generate(code)).sequence for code in (1, 1, 2, 1)
    ]
    assert sequences == [1, 2, 1, 3]


def test_previous_is_the_last_uuid_generated_for_that_code() -> None:
    generator = SemanticUUIDGenerator(scenario_id=0x5001)
    generator.generate(1)
    last = generator.generate(1)
    generator.generate(2)
    assert generator.previous(1) == last


def test_previous_before_any_uuid_names_the_code() -> None:
    with pytest.raises(LookupError, match="0x1020304"):
        SemanticUUIDGenerator(scenario_id=0x5001).previous(0x01020304)


def test_a_scenarios_rows_of_a_table_lie_within_its_bounds() -> None:
    """Of any version and sequence; byte order is PostgreSQL's order for a uuid."""
    low, high = scenario_bounds(0x01020304, 0x5001)
    inside = [
        SemanticUUIDGenerator(0x5001, version).generate(0x01020304, sequence=sequence)
        for version in (0, 2**12 - 1)
        for sequence in (0, 2**62 - 1)
    ]
    assert all(low.bytes <= uuid.bytes <= high.bytes for uuid in inside)
    assert (low, high) == (min(inside, key=_bytes), max(inside, key=_bytes))


@pytest.mark.parametrize(
    ("table_code", "scenario_id"),
    [(0x01020303, 0x5001), (0x01020304, 0x5002), (0x01020305, 0x5001), (0x01020304, 0x5000)],
)
def test_another_table_or_scenario_lies_outside(table_code: int, scenario_id: int) -> None:
    low, high = scenario_bounds(0x01020304, 0x5001)
    other = SemanticUUIDGenerator(scenario_id).generate(table_code, sequence=1)
    assert not low.bytes <= other.bytes <= high.bytes


def _bytes(uuid: UUID) -> bytes:
    return uuid.bytes
