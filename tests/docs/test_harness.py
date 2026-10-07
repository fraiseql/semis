"""The harness finds every block a document holds, however its fence is written."""

from pathlib import Path

import pytest

from tests.docs import test_examples
from tests.docs.test_examples import Block, _blocks


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(test_examples, "ROOT", tmp_path)
    return tmp_path


def test_a_titled_block_is_found(root: Path) -> None:
    (root / "page.md").write_text('Intro.\n\n```yaml title="semis.yaml"\nscenarios: s/\n```\n')
    (block,) = _blocks("page.md")
    assert (block.line, block.language, block.text) == (3, "yaml", "scenarios: s/")


def test_a_titled_block_names_its_file(root: Path) -> None:
    (root / "page.md").write_text('```sql title="db/schema/010.sql" {2}\nSELECT 1;\n```\n')
    (block,) = _blocks("page.md")
    assert block.title == "db/schema/010.sql"


def test_the_sites_pages_are_documents(root: Path) -> None:
    pages = root / "site" / "src" / "content" / "docs"
    (pages / "guides").mkdir(parents=True)
    for page in ("index.mdx", "guides/ci.md", "guides/notes.txt"):
        (pages / page).write_text("---\ntitle: T\n---\n")
    assert test_examples._documents() == (
        "README.md",
        "docs/ARCHITECTURE.md",
        "docs/PRD.md",
        "site/src/content/docs/guides/ci.md",
        "site/src/content/docs/index.mdx",
    )


def test_a_block_covered_from_another_document_repeats_it() -> None:
    shown = Block("README.md", 1, "bash", "uv add fraiseql-semis")
    same = Block("site/a.md", 1, "bash", "uv add fraiseql-semis")
    drifted = Block("site/b.md", 1, "bash", "uv add fraiseql-semis\nuv sync")
    checks = dict.fromkeys((same.key, drifted.key), shown.key)
    assert test_examples._unrepeated([shown, same, drifted], checks) == [drifted.key]


def test_blocks_open_alike_only_when_their_documents_check_reads_them() -> None:
    walkthrough = Block("site/a.md", 1, "bash", "semis seeds s.yaml")
    shown = [Block("site/a.md", n, "text", f"scenario s is unpinned\nwrote {n}") for n in (2, 3)]
    elsewhere = [Block("site/b.md", n, "text", "scenario s is unpinned") for n in (4, 5)]
    checks: dict[tuple[str, str], object] = {walkthrough.key: print}
    checks[shown[0].key] = walkthrough.key
    checks[elsewhere[0].key] = print
    blocks = [walkthrough, *shown, *elsewhere]
    assert test_examples._ambiguous(blocks, checks) == [elsewhere[0].key]
