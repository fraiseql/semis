"""The semantic UUID: table code, scenario, version, sequence — 4/2/2/8 bytes."""

from collections.abc import Callable

import pytest

from fraiseql_semis.uuid_generator import SemanticUUIDGenerator


def test_round_trip_encodes_four_fields() -> None:
    generator = SemanticUUIDGenerator(scenario_id=0x5001, version=1)
    encoded = generator.generate(0x01020304, sequence=66)
    assert SemanticUUIDGenerator.decode(encoded) == (0x01020304, 0x5001, 1, 66)


@pytest.mark.parametrize(
    "fields",
    [
        (0, 0, 0, 0),
        (2**32 - 1, 2**16 - 1, 2**16 - 1, 2**64 - 1),
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
        ("version", lambda: SemanticUUIDGenerator(1, version=2**16)),
        ("sequence", lambda: SemanticUUIDGenerator(1).generate(1, sequence=2**64)),
    ],
)
def test_refuses_a_field_too_wide(field: str, build: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match=field):
        build()


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
