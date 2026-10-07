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


def test_a_tree_is_every_sql_file_under_it_sorted_by_path(tmp_path: Path) -> None:
    (tmp_path / "b").mkdir()
    (tmp_path / "sub").mkdir()
    (tmp_path / "10.sql").write_text("CREATE SCHEMA s; CREATE TABLE s.t1 (a int);")
    (tmp_path / "b" / "05.sql").write_text("CREATE TABLE s.t3 (a int);")
    (tmp_path / "sub" / "20.sql").write_text("CREATE TABLE s.t2 (a int);")
    (tmp_path / "x.txt").write_text("CREATE TABLE s.ignored (a int);")
    tables = [ref.display for ref in parse_schema(tmp_path).tables]
    assert tables == ["s.t1", "s.t3", "s.t2"]


def test_a_string_in_a_sequence_is_a_path(tmp_path: Path) -> None:
    (tmp_path / "10.sql").write_text("CREATE SCHEMA s; CREATE TABLE s.t1 (a int);")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "20.sql").write_text("CREATE TABLE s.t2 (a int);")
    model = parse_schema([tmp_path / "10.sql", str(tmp_path / "sub")])
    assert [ref.display for ref in model.tables] == ["s.t1", "s.t2"]
