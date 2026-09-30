"""The table-code registry: qualified name → the UUID's first 32 bits."""

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import CodeRegistryError
from fraiseql_semis.uuid_generator import SemanticUUIDGenerator


def test_resolves_a_qualified_name() -> None:
    assert TableCodes({"catalog.tb_continent": 0x02030405}).code_for("catalog.tb_continent") == (
        0x02030405
    )


def test_unknown_table_names_itself() -> None:
    codes = TableCodes({"catalog.tb_continent": 0x02030405})
    with pytest.raises(CodeRegistryError, match=r"catalog\.tb_country"):
        codes.code_for("catalog.tb_country")


def test_duplicate_code_is_refused() -> None:
    with pytest.raises(CodeRegistryError, match=r"catalog\.tb_city.*etl\.tb_city"):
        TableCodes({"catalog.tb_city": 0x0A, "etl.tb_city": 0x0A})


def test_uuid_text_shows_the_registered_hex_digits() -> None:
    codes = TableCodes({"catalog.tb_language": 0x01020304})
    generator = SemanticUUIDGenerator(scenario_id=0x5001)
    encoded = generator.generate(codes.code_for("catalog.tb_language"), sequence=0x42)
    assert str(encoded) == "01020304-5001-0001-0000-000000000042"


def test_bare_table_name_is_refused() -> None:
    with pytest.raises(CodeRegistryError, match="tb_city"):
        TableCodes({"tb_city": 0x0A})


@pytest.mark.parametrize("code", [-1, 2**32])
def test_code_the_uuid_cannot_hold_is_refused(code: int) -> None:
    with pytest.raises(CodeRegistryError, match=r"catalog\.tb_city"):
        TableCodes({"catalog.tb_city": code})


def test_refusals_say_what_to_do() -> None:
    with pytest.raises(CodeRegistryError) as refused:
        TableCodes({}).code_for("catalog.tb_city")
    assert refused.value.resolution_hint
