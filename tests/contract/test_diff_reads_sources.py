"""diff compares DDL sources, and parse_schema reads a tree by one rule (ARCHITECTURE §8).

A pin refusal names what moved by calling ``diff`` on the pinned DDL snapshot and the
current source, and the snapshot of a tree is written by the rule ``parse_schema`` reads
it with. These pin both, so a change to either fails here by name.
"""

from pathlib import Path

import pytest
from confiture.platform import diff, parse_schema

from tests.ddl import TRINITY

NOT_NULL = TRINITY.replace("name VARCHAR(50) NOT NULL", "name VARCHAR(50)")


def test_diff_takes_sources_not_models() -> None:
    model = parse_schema(TRINITY)
    with pytest.raises(TypeError):
        diff(model, model)  # ty: ignore[invalid-argument-type]


def test_diff_names_a_nullability_change_in_one_line() -> None:
    changes = [str(change) for change in diff(NOT_NULL, TRINITY).changes]
    assert changes == ["CHANGE COLUMN NULLABLE catalog.tb_continent.name FROM true TO false"]


def test_diff_of_one_source_reports_nothing() -> None:
    assert diff(TRINITY, TRINITY).changes == []


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
