"""How parse_schema reads a schema — the facts SchemaFacts' constructors rest on (D13)."""

from pathlib import Path

import pytest
from confiture.platform import ConfigurationError, parse_schema

from tests.ddl import TRINITY


def test_ddl_text_is_a_schema() -> None:
    assert parse_schema(TRINITY).tables


def test_env_outside_a_project_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="Environment config not found"):
        parse_schema(env="development", project_dir=tmp_path)


def test_a_source_and_an_env_together_are_refused() -> None:
    with pytest.raises(ValueError, match="exactly one of a schema source or an environment"):
        parse_schema(TRINITY, env="development")


def test_a_routine_returning_a_wide_table_reads() -> None:
    """An input parameter and 21+ output columns: a RecursionError until #457."""
    columns = ", ".join(f"c{n} TEXT" for n in range(25))
    ddl = (
        f"CREATE FUNCTION catalog.fn_wide(p_id UUID) RETURNS TABLE ({columns})\n"
        "LANGUAGE sql AS $$ SELECT NULL::TEXT $$;\n"
    )
    assert parse_schema(TRINITY + ddl).tables
