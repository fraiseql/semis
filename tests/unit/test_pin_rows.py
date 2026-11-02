"""A prep-seed pin's rows: the existing rows a scenario points at, kept by UUID."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from fraiseql_semis.codes import TableCodes
from fraiseql_semis.errors import ScenarioError
from fraiseql_semis.pin import PinnedRead, SchemaPin, merge_reads
from fraiseql_semis.scenario import ScenarioManager
from fraiseql_semis.schema import SchemaFacts
from tests.ddl import CODES, TRINITY

BOOKS, MUSIC, FILM = (f"00000000-0000-4000-8000-00000000000{n}" for n in (1, 2, 3))
CATEGORIES = PinnedRead(
    "shop.tb_category", "identifier", None, (("books", BOOKS), ("music", MUSIC))
)
# One table read twice, by two columns: a translation's locale table, and its existing rows.
BY_CODE = PinnedRead("shop.tb_locale", "code", ("fr", "en"), (("fr", FILM), ("en", BOOKS)))
BY_SLUG = PinnedRead("shop.tb_locale", "identifier", None, (("fr-fr", FILM),))
PIN = replace(
    SchemaPin.of(
        SchemaFacts.from_source(TRINITY, table_codes=TableCodes(CODES)), ["catalog.tb_continent"]
    ),
    rows=(CATEGORIES, BY_CODE, BY_SLUG),
)


def test_a_pin_keeps_its_rows_and_reads_them_back() -> None:
    document = json.loads(json.dumps(PIN.to_document()))
    assert SchemaPin.from_document(document, scenario="s") == PIN
    assert [read["table"] for read in document["rows"]["reads"]] == [
        "shop.tb_category",
        "shop.tb_locale",
        "shop.tb_locale",
    ]


def test_a_pin_without_rows_keeps_no_rows_section() -> None:
    assert "rows" not in replace(PIN, rows=None).to_document()


def _one_row(row: list[object]) -> dict[str, object]:
    """A rows section holding one read of one *row*."""
    return {"digest": "x", "reads": [{"table": "t", "by": None, "where": None, "rows": [row]}]}


@pytest.mark.parametrize(
    ("rows", "match"),
    [
        ({"digest": "sha256:0", "reads": []}, "rows do not hold what their digest was taken from"),
        ({"reads": []}, "rows are malformed"),
        ({"digest": "x", "reads": [{"table": "t"}]}, "rows are malformed"),
        ({"digest": "x", "reads": "all"}, "rows are malformed"),
        (
            {"digest": "x", "reads": [{"table": "t", "by": 3, "where": None, "rows": []}]},
            "rows are malformed",
        ),
        (_one_row(["a", "b"]), "rows are malformed"),
        (_one_row([[1], BOOKS]), "rows are malformed"),
        (_one_row(["a", 1]), "rows are malformed"),
    ],
    ids=[
        "digest",
        "no-digest",
        "read-keys",
        "reads",
        "read-types",
        "not-a-uuid",
        "name-type",
        "uuid-type",
    ],
)
def test_rows_that_do_not_read_are_refused(rows: dict[str, object], match: str) -> None:
    document = PIN.to_document() | {"rows": rows}
    with pytest.raises(ScenarioError, match=rf"^scenario s: its pin file's {match}"):
        SchemaPin.from_document(document, scenario="s")


def test_a_whole_table_keeps_its_order_and_appends_what_it_gained() -> None:
    """Rows already recorded keep their place, whatever order the database gives them."""
    now = replace(CATEGORIES, rows=(("film", FILM), ("music", MUSIC), ("books", BOOKS)))
    merged = merge_reads((CATEGORIES,), (now,))
    assert merged.reads == (replace(CATEGORIES, rows=(*CATEGORIES.rows, ("film", FILM))),)
    assert (merged.stale, merged.added, merged.missing) == (
        (),
        ("shop.tb_category: 1 row added",),
        (),
    )


def test_the_same_rows_in_another_order_merge_to_the_pinned_ones() -> None:
    now = replace(CATEGORIES, rows=tuple(reversed(CATEGORIES.rows)))
    merged = merge_reads((CATEGORIES,), (now,))
    assert (merged.reads, merged.stale, merged.added) == ((CATEGORIES,), (), ())


def test_a_row_gone_from_a_whole_table_is_stale() -> None:
    now = replace(CATEGORIES, rows=(("music", MUSIC),))
    merged = merge_reads((CATEGORIES,), (now,))
    assert merged.stale == (f"shop.tb_category: books {BOOKS} is gone",)
    assert merged.reads == (now,)


def test_a_named_row_whose_uuid_changed_is_stale() -> None:
    now = replace(BY_CODE, rows=(("fr", MUSIC), ("en", BOOKS)))
    merged = merge_reads((BY_CODE,), (now,))
    assert (merged.reads, merged.stale) == ((now,), (f"shop.tb_locale: fr {FILM} → {MUSIC}",))


def test_a_read_the_pin_lacks_is_missing() -> None:
    merged = merge_reads((CATEGORIES,), (CATEGORIES, BY_CODE))
    assert merged.missing == ("shop.tb_locale: not pinned, by code",)


def test_an_existing_table_without_a_uuid_id_cannot_be_pinned(tmp_path: Path) -> None:
    """A prep-seed child carries its parent's UUID: a row with none cannot be pointed at.
    Refused before the database is asked."""
    ddl = TRINITY.replace(
        "CREATE TABLE catalog.tb_continent (\n    pk_continent BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,\n    id UUID NOT NULL UNIQUE,",
        "CREATE TABLE catalog.tb_continent (\n    pk_continent BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,\n    id TEXT NOT NULL UNIQUE,",
    )
    path = tmp_path / "countries.yaml"
    path.write_text(
        "scenario_id: 0x5001\nname: countries\nmode: prep-seed\ntables:\n"
        "  - name: catalog.tb_country\n    count: 1\nexisting:\n  - name: catalog.tb_continent\n"
    )
    manager = ScenarioManager(SchemaFacts.from_source(ddl, table_codes=TableCodes(CODES)))
    with pytest.raises(
        ScenarioError,
        match=r"^scenario countries: existing catalog\.tb_continent has no uuid id for a "
        r"prep-seed child to carry",
    ):
        manager.pin(manager.load(path), path, connection=object())  # type: ignore[arg-type]
