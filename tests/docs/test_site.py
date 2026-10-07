"""The site: its pages in the house style of fraiseql.dev, and its release checked."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]


def test_the_release_checks_the_version_as_the_site_title_spells_it() -> None:
    title = (ROOT / "site" / "src" / "components" / "SiteTitle.astro").read_text()
    spelled = re.search(r"aria-label=\{`(.*)\$\{SEMIS_VERSION\}(.*)`\}", title)
    assert spelled is not None
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "publish.yml").read_text())
    (check,) = [
        step["run"] for step in workflow["jobs"]["site"]["steps"] if "grep" in step.get("run", "")
    ]
    assert f'"{spelled.group(1)}${{GITHUB_REF_NAME#v}}{spelled.group(2)}"' in check


# fraiseql.dev's STYLE.md, the rules a page's text can be held to --------------------

PAGES = ROOT / "site" / "src" / "content" / "docs"
MARKETING = ("easily", "simply", "just", "powerful", "seamless", "blazing")


def _pages() -> list[Path]:
    return sorted(path for suffix in ("*.md", "*.mdx") for path in PAGES.rglob(suffix))


def _frontmatter(text: str) -> dict[str, str]:
    _, front, _ = text.split("---\n", 2)
    loaded = yaml.safe_load(front)
    assert isinstance(loaded, dict)
    return loaded


def _prose(text: str) -> str:
    """*text* without its frontmatter, its fenced blocks and its inline code."""
    body = text.split("---\n", 2)[2]
    body = re.sub(r"^([ \t]*)```.*?^\1```", "", body, flags=re.M | re.S)
    return re.sub(r"`[^`]*`", "", body)


@pytest.mark.parametrize("page", _pages(), ids=lambda path: str(path.relative_to(PAGES)))
def test_a_page_follows_the_house_style(page: Path) -> None:
    text = page.read_text()
    front = _frontmatter(text)
    assert 0 < len(front["title"]) <= 60
    assert 0 < len(front["description"]) <= 155 and not front["description"].endswith(".")
    prose = _prose(text)
    assert not re.search(r"^# ", prose, re.M), "the H1 is the frontmatter's title"
    assert "!" not in prose.replace("<!--", "")
    assert [word for word in MARKETING if re.search(rf"\b{word}\b", prose, re.I)] == []
    openings = re.findall(r"^\s*```(.*)$", text, re.M)[::2]
    assert all(re.match(r"\w+", opening) for opening in openings), "a fence names its language"
    assert not re.search(r"^```bash\n\$ ", text, re.M), "no prompt in a shell block"
    *_, last = re.split(r"^## ", text, flags=re.M)
    assert last.startswith("Next steps\n")
    assert 3 <= len(re.findall(r"^- \[", last, re.M)) <= 5


def test_every_page_is_linked_from_another() -> None:
    linked = set()
    for page in _pages():
        linked.update(re.findall(r"\]\((/[^)#]*)", page.read_text()))
    slugs = set()
    for page in _pages():
        slug = "/" + str(page.relative_to(PAGES).with_suffix("")).removesuffix("index") + "/"
        slugs.add(slug.replace("//", "/"))
    assert sorted(slugs - linked - {"/"}) == []
