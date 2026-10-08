"""Every example in README, ARCHITECTURE, PRD and the site is true of the code that ships.

Each fenced block is found by its document and its first line, and checked: Python is
run, YAML is loaded as a scenario or a project, a command is parsed by the command it
names, and an output is compared with what shipped code produces. A confiture behaviour
ARCHITECTURE shows is pinned by a contract test the document names. A block no check
covers fails, and so does a check whose block is gone.
"""

import doctest
import inspect
import json
import re
import shlex
import shutil
import subprocess
import textwrap
import tomllib
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import redirect_stdout
from dataclasses import dataclass
from functools import cached_property
from io import StringIO
from pathlib import Path
from typing import LiteralString, cast
from urllib.parse import urlsplit

import psycopg
import pytest
import yaml
from confiture import platform
from psycopg import sql
from typer.main import get_group
from typer.testing import CliRunner, Result

import fraiseql_semis
from fraiseql_semis import (
    Deleted,
    FakeDataGenerator,
    Library,
    PrepSeedResolver,
    Project,
    ProjectSchema,
    ScenarioManager,
    SchemaFacts,
    SemanticUUIDGenerator,
    TableCodes,
    errors,
    readback,
    seeds,
)
from fraiseql_semis.cli import app
from fraiseql_semis.cli.main import _deleted
from fraiseql_semis.project import ENTRY_POINTS
from fraiseql_semis.providers import SHIPPED
from tests.ddl import CODES, HIERARCHY, HIERARCHY_CODES, TRINITY, WORKED

ROOT = Path(__file__).parents[2]
PAGES = Path("site/src/content/docs")
"""The site's pages, each a document: every .md and .mdx under it."""


def _documents() -> tuple[str, ...]:
    pages = sorted(
        str(path.relative_to(ROOT))
        for suffix in ("*.md", "*.mdx")
        for path in (ROOT / PAGES).rglob(suffix)
    )
    return ("README.md", "docs/ARCHITECTURE.md", "docs/PRD.md", *pages)


DOCUMENTS = _documents()
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())


@dataclass(frozen=True)
class Block:
    document: str
    line: int
    language: str
    text: str
    meta: str = ""
    """What the fence says after its language: ``title="semis.yaml"``."""

    @property
    def key(self) -> tuple[str, str]:
        return self.document, self.text.splitlines()[0].strip()

    @property
    def title(self) -> str | None:
        """The file the block is, as its fence's ``title=`` names it."""
        found = re.search(r'\btitle="([^"]+)"', self.meta)
        return found.group(1) if found else None


def _blocks(document: str) -> Iterator[Block]:
    lines = (ROOT / document).read_text().splitlines()
    index = 0
    while index < len(lines):
        opening = re.fullmatch(r"(\s*)```(\w*)(?:\s+(.*))?", lines[index])
        if opening:
            end = next(i for i in range(index + 1, len(lines)) if lines[i].strip() == "```")
            text = textwrap.dedent("\n".join(lines[index + 1 : end]))
            yield Block(document, index + 1, opening.group(2), text, opening.group(3) or "")
            index = end
        index += 1


BLOCKS = [block for document in DOCUMENTS for block in _blocks(document)]


@dataclass
class Context:
    """What a check may use: a scratch directory, the monkeypatch, the database."""

    tmp_path: Path
    monkeypatch: pytest.MonkeyPatch
    request: pytest.FixtureRequest

    def database(self) -> str:
        """The URL of a database of its own, dropped when the test ends: examples name
        `catalog`, and a command takes a URL."""
        url: str = self.request.getfixturevalue("database_url")
        name = f"semis_docs_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))

        def drop() -> None:
            with psycopg.connect(url, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))

        self.request.addfinalizer(drop)
        parts = urlsplit(url)  # urlunsplit would drop the empty host of postgresql:///x
        return f"{parts.scheme}://{parts.netloc}/{name}" + (
            f"?{parts.query}" if parts.query else ""
        )


Check = Callable[[Block, Context], None]
CHECKS: dict[tuple[str, str], Check | tuple[str, str]] = {}
"""Each block's check, or the key of the block whose check covers it too."""
NEEDS_DATABASE: set[tuple[str, str]] = set()


def check(document: str, first: str, *, database: bool = False) -> Callable[[Check], Check]:
    def register(function: Check) -> Check:
        CHECKS[document, first] = function
        if database:
            NEEDS_DATABASE.add((document, first))
        return function

    return register


def covered(document: str, first: str, by: tuple[str, str]) -> None:
    CHECKS[document, first] = by


def pinned(document: str, first: str, contract: str) -> None:
    """A confiture behaviour the document shows, pinned by the contract test it names."""

    def named(block: Block, _context: Context) -> None:
        path, _, test = contract.partition("::")
        assert f"def {test}(" in (ROOT / path).read_text(), f"{contract} is gone"
        assert Path(path).name in (ROOT / block.document).read_text(), (
            f"{block.document} does not name {path}"
        )

    check(document, first)(named)


def _facts(ddl: str, codes: dict[str, int]) -> SchemaFacts:
    return SchemaFacts.from_source(ddl, table_codes=TableCodes(codes))


def _scenario_file(tmp_path: Path, tables: str, *, mode: str = "prep-seed") -> Path:
    path = tmp_path / "scenario.yaml"
    path.write_text(f"scenario_id: 0x5001\nname: doc\nmode: {mode}\nseed: 42\ntables:\n{tables}")
    return path


def _checked(manager: ScenarioManager, path: Path) -> None:
    manager.check(manager.load(path), no_pin=True)


def _psql(text: str) -> tuple[list[str], list[tuple[str, ...]]]:
    """The columns and rows of a table as psql prints one, each value as printed."""
    header, _rule, *rows, _count = text.splitlines()
    columns = [name.strip() for name in header.split("|")]
    return columns, [tuple(value.strip() for value in row.split("|")) for row in rows]


def _doctest(block: Block, globs: dict[str, object]) -> None:
    parser = doctest.DocTestParser()
    test = parser.get_doctest(block.text, globs, block.key[1], block.document, block.line)
    runner = doctest.DocTestRunner(optionflags=doctest.NORMALIZE_WHITESPACE)
    out = StringIO()
    runner.run(test, out=out.write)
    assert runner.failures == 0, out.getvalue()


# README ---------------------------------------------------------------------------------


_INSTALL = "uv add fraiseql-semis          # brings fraiseql-confiture>=1.30,<2"


@check("README.md", _INSTALL)
def _install(block: Block, _context: Context) -> None:
    command, _, comment = block.text.partition("#")
    assert command.split() == ["uv", "add", PYPROJECT["project"]["name"]]
    confiture = next(d for d in PYPROJECT["project"]["dependencies"] if "confiture" in d)
    assert comment.split()[-1] == confiture


CONTINENT = TRINITY[: TRINITY.index("CREATE TABLE catalog.tb_country")]


@check("README.md", "from pathlib import Path", database=True)
def _chain(block: Block, context: Context) -> None:
    """The chain, run as written into a database of its own, and what psql shows after."""
    url = context.database()
    (context.tmp_path / "db" / "schema").mkdir(parents=True)
    (context.tmp_path / "db" / "schema" / "010_catalog.sql").write_text(CONTINENT)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(CONTINENT)
    context.monkeypatch.chdir(context.tmp_path)
    code = block.text.replace('"postgresql:///myproject_dev"', repr(url))
    exec(compile(code, "README.md", "exec"), {})
    shown = next(b for b in BLOCKS if b.key == ("README.md", _PSQL))
    columns, expected = _psql(shown.text)
    with psycopg.connect(url) as connection:
        found = connection.execute(
            sql.SQL("SELECT {} FROM catalog.tb_continent ORDER BY 1").format(
                sql.SQL(", ").join(map(sql.Identifier, columns))
            )
        ).fetchall()
    assert [tuple(str(value) for value in row) for row in found] == expected


_PSQL = "pk_continent |                  id                  | identifier |    name"
covered("README.md", _PSQL, by=("README.md", "from pathlib import Path"))


def _refusal(block: Block, context: Context) -> None:
    """``write_copy_seed`` refuses the identity column with the message the document shows."""
    call, refusal = block.text.split("\n", 1)
    rows = [{"pk_continent": 1, "id": "02030405-5001-8001-8000-000000000001"}]
    names = {
        "write_copy_seed": platform.write_copy_seed,
        "path": context.tmp_path / "seed.sql",
        "rows": rows,
        "model": platform.parse_schema(TRINITY),
    }
    with pytest.raises(platform.SeedError) as refused:
        eval(call.removeprefix(">>> "), names)
    assert " ".join(refusal.removeprefix("SeedError:").split()) == str(refused.value)
    assert not names["path"].exists()  # type: ignore[attr-defined]


_WRITE_PK = (
    '>>> write_copy_seed(path, "catalog.tb_continent", ["pk_continent", "id"], rows, model=model)'
)
check("README.md", _WRITE_PK)(_refusal)


def _project(tmp_path: Path) -> Path:
    """A project over the worked schema, its scenarios the ones this repository ships."""
    (tmp_path / "schema.sql").write_text(WORKED)
    (tmp_path / "scenarios").mkdir()
    shipped = (ROOT / "scenarios" / "minimal_seed.yaml").read_text()
    (tmp_path / "scenarios" / "minimal_seed.yaml").write_text(shipped)
    codes = "".join(f"  {table}: {code:#010x}\n" for table, code in CODES.items())
    (tmp_path / "semis.yaml").write_text(
        f"schema: {{ddl: schema.sql}}\nscenarios: scenarios/\ntable_codes:\n{codes}"
    )
    return tmp_path / "semis.yaml"


@check("README.md", "$ semis decode-uuid 02030405-5001-8001-8000-000000000001")
def _decode(block: Block, context: Context) -> None:
    command, *shown = block.text.splitlines()
    _project(context.tmp_path)
    context.monkeypatch.chdir(context.tmp_path)
    result = CliRunner().invoke(app, shlex.split(command)[2:])
    assert result.exit_code == 0, result.output
    assert [line.rstrip() for line in result.output.splitlines()] == shown


_AGAIN = "semis apply scenarios/minimal_seed.yaml -o db/seeds/again --database-url postgresql:///myproject_dev"
_RESET = "semis apply scenarios/minimal_seed.yaml --reset -o db/seeds/again --database-url postgresql:///myproject_dev"


@check("README.md", _AGAIN, database=True)
def _again(block: Block, context: Context) -> None:
    """Applied once, then again as the block shows: refused as the next block prints,
    nothing written; then applied with --reset, as the block after says, and the
    scenario's rows are the first run's count again."""
    url = context.database()
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(_page_sql(WORKED))
    _project(context.tmp_path)
    context.monkeypatch.chdir(context.tmp_path)

    def semis(line: str) -> Result:
        words = [
            url if word == "postgresql:///myproject_dev" else word for word in shlex.split(line)
        ]
        return CliRunner().invoke(app, words[1:])

    assert semis(_AGAIN.replace("again", "run")).exit_code == 0
    refused = semis(block.text)
    printed = next(b for b in BLOCKS if b.key == ("README.md", _REFUSED))
    assert (refused.exit_code, refused.stderr.splitlines()) == (1, printed.text.splitlines())
    assert not Path("db/seeds/again").exists()
    (reset,) = next(b for b in BLOCKS if b.key == ("README.md", _RESET)).text.splitlines()
    assert "semis apply --reset" in refused.stderr
    again = semis(reset)
    assert (again.exit_code, again.output.splitlines()[-1]) == (0, "committed"), again.output
    with psycopg.connect(url) as connection:
        (twins,) = connection.execute("SELECT count(*) FROM prep_seed.tb_continent").fetchone()
    assert twins == 7


_REFUSED = "scenario minimal_seed is already applied: prep_seed.tb_continent holds its rows"
covered("README.md", _REFUSED, by=("README.md", _AGAIN))
covered("README.md", _RESET, by=("README.md", _AGAIN))


# fraiseql/semis#2's shape: reference rows the schema's own DDL inserts.
SHOP_REFERENCES = """CREATE SCHEMA shop;
CREATE TABLE shop.tb_category (pk_category bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE, identifier text NOT NULL UNIQUE);
CREATE TABLE shop.tb_country (pk_country bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE, identifier text NOT NULL UNIQUE);
CREATE TABLE shop.tb_customer (pk_customer bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id uuid NOT NULL UNIQUE, identifier text NOT NULL UNIQUE, name text NOT NULL,
  fk_category bigint NOT NULL REFERENCES shop.tb_category,
  fk_country bigint NOT NULL REFERENCES shop.tb_country);"""
SHOP_ROWS = """INSERT INTO shop.tb_category (identifier) VALUES ('books'), ('music');
INSERT INTO shop.tb_country (identifier) VALUES ('de'), ('es'), ('fr'), ('it');"""


@check("README.md", "existing:", database=True)
def _existing(block: Block, context: Context) -> None:
    """Six customers over the rows the DDL inserted: every category in pk_* order, the
    countries the where: names, in its order; nothing generated again, no code needed."""
    url = context.database()
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(_page_sql(SHOP_REFERENCES + "\n" + SHOP_ROWS))
    manager = ScenarioManager(_facts(SHOP_REFERENCES, {"shop.tb_customer": 0x0B}))
    tables = "  - name: shop.tb_customer\n    count: 6\n"
    path = _scenario_file(context.tmp_path, tables + block.text + "\n", mode="read-back")
    with psycopg.connect(url) as connection:
        manager.execute(manager.load(path), context.tmp_path / "out", connection=connection)
        pointed = connection.execute(
            "SELECT g.identifier, k.identifier FROM shop.tb_customer AS c"
            " JOIN shop.tb_category AS g ON g.pk_category = c.fk_category"
            " JOIN shop.tb_country AS k ON k.pk_country = c.fk_country ORDER BY c.id"
        ).fetchall()
        (categories,) = connection.execute("SELECT count(*) FROM shop.tb_category").fetchone() or (
            0,
        )
    assert pointed == [
        ("books", "fr"), ("music", "de"), ("books", "es"),
        ("music", "fr"), ("books", "de"), ("music", "es"),
    ]  # fmt: skip
    assert categories == 2


@check("README.md", "# scenarios/minimal_seed.yaml")
def _minimal_seed(block: Block, _context: Context) -> None:
    assert block.text + "\n" == (ROOT / "scenarios" / "minimal_seed.yaml").read_text()
    manager = ScenarioManager(_facts(WORKED, CODES))
    _checked(manager, ROOT / "scenarios" / "minimal_seed.yaml")


WAREHOUSE = ROOT / "tests" / "warehouse"


@check("README.md", "- name: inventory.tb_item")
def _left_null(block: Block, context: Context) -> None:
    """The warehouse's scenario, its item written as the document writes it."""
    stock = (WAREHOUSE / "scenarios" / "stock.yaml").read_text()
    tables = stock[: stock.index("  - name: inventory.tb_item")]
    path = context.tmp_path / "stock.yaml"
    path.write_text(tables + textwrap.indent(block.text, "  ") + "\n")
    _checked(Project.load(WAREHOUSE / "semis.yaml").manager(), path)


@check("README.md", "from fraiseql_semis import ScenarioManager")
def _manager(block: Block, context: Context) -> None:
    (context.tmp_path / "scenarios").mkdir()
    shipped = (ROOT / "scenarios" / "minimal_seed.yaml").read_text()
    (context.tmp_path / "scenarios" / "minimal_seed.yaml").write_text(shipped)
    context.monkeypatch.chdir(context.tmp_path)
    printed = StringIO()
    with redirect_stdout(printed):
        exec(block.text, {"facts": _facts(WORKED, CODES), "Path": Path})
    comment = block.text.splitlines()[-1].partition("#")[2].strip()
    assert printed.getvalue().strip() == comment


@check("README.md", "# semis.yaml")
def _semis_yaml(block: Block, context: Context) -> None:
    """It loads, its own providers imported from the module it names."""
    package = context.tmp_path / "myproject"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "fake.py").write_text("PROVIDERS = {'slogan': lambda faker, column: 'x'}\n")
    context.monkeypatch.syspath_prepend(str(context.tmp_path))
    (context.tmp_path / "db" / "0_schema").mkdir(parents=True)
    (context.tmp_path / "semis.yaml").write_text(block.text)
    project = Project.load(context.tmp_path / "semis.yaml")
    assert project.libraries == (SHIPPED["i18n"], SHIPPED["organization"])
    assert list(project.providers) == ["slogan"]
    assert isinstance(project.schema, ProjectSchema)
    assert project.schema.env == "development"


@check(
    "README.md",
    "semis seeds scenarios/minimal_seed.yaml -o db/seeds/prep   # prep-seed: writes files, no database",
)
def _commands(block: Block, context: Context) -> None:
    """Each command line parses: its command exists, and so does every option it gives."""
    _project(context.tmp_path)
    for directory in ("db/seeds/prep", "db/seeds/run", "out"):
        (context.tmp_path / directory).mkdir(parents=True)
    context.monkeypatch.chdir(context.tmp_path)
    for line in block.text.splitlines():
        _parses(shlex.split(line, comments=True))


def _parses(words: list[str]) -> None:
    """A semis command line parses: its command exists, and so does every option given."""
    program, name, *arguments = words
    assert program == "semis"
    command = get_group(app).commands.get(name)
    assert command is not None, f"semis has no {name}"
    command.make_context(name, arguments)


@check("README.md", "- name: catalog.tb_currency")
def _named_provider(block: Block, context: Context) -> None:
    ddl = "CREATE SCHEMA catalog; CREATE TABLE catalog.tb_currency (pk_currency BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, id UUID NOT NULL UNIQUE, iso_code CHAR(3) NOT NULL, name TEXT NOT NULL);"
    manager = ScenarioManager(
        _facts(ddl, {"catalog.tb_currency": 0x0A}),
        providers={"slogan": lambda _faker, _column: "x"},
        libraries=[SHIPPED["i18n"]],
    )
    tables = textwrap.indent(block.text, "  ")
    _checked(manager, _scenario_file(context.tmp_path, tables, mode="read-back"))


_ACME = "# acme_semis/__init__.py"
_ENTRY = "# acme-semis's pyproject.toml"


@check("README.md", _ACME)
def _library(_block: Block, context: Context) -> None:
    """The two blocks as a package: installed, it is enabled by its short name."""
    _acme_installed(context)
    _project(context.tmp_path)
    with (context.tmp_path / "semis.yaml").open("a") as project:
        project.write("providers: [i18n, acme]\n")
    library = Project.load(context.tmp_path / "semis.yaml").libraries[1]
    assert isinstance(library, Library)
    assert (library.name, sorted(library.providers)) == ("acme", ["ticker"])


def _acme_installed(context: Context) -> None:
    """The README's library and its entry point, installed as a package would be."""
    library = next(b for b in BLOCKS if b.key == ("README.md", _ACME))
    entry = next(b for b in BLOCKS if b.key == ("README.md", _ENTRY))
    site = context.tmp_path / "site"
    (site / "acme_semis").mkdir(parents=True)
    (site / "acme_semis" / "__init__.py").write_text(library.text)
    info = site / "acme_semis-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: acme-semis\nVersion: 1.0\n")
    points = tomllib.loads(entry.text)["project"]["entry-points"][ENTRY_POINTS]
    (info / "entry_points.txt").write_text(
        f"[{ENTRY_POINTS}]\n" + "".join(f"{k} = {v}\n" for k, v in points.items())
    )
    context.monkeypatch.syspath_prepend(str(site))


covered("README.md", _ENTRY, by=("README.md", _ACME))


def _hierarchy(block: Block, context: Context) -> None:
    manager = ScenarioManager(_facts(HIERARCHY, HIERARCHY_CODES))
    tables = textwrap.indent(block.text, "  ")
    _checked(manager, _scenario_file(context.tmp_path, tables, mode="read-back"))


check("README.md", "- name: catalog.tb_location")(_hierarchy)
check("README.md", "- name: catalog.tb_location        # a flat set: no row has a parent")(
    _hierarchy
)


@check("README.md", "uv sync --all-extras")
def _development(block: Block, _context: Context) -> None:
    dev = " ".join(PYPROJECT["project"]["optional-dependencies"]["dev"])
    for line in block.text.splitlines():
        words = shlex.split(line, comments=True)
        if words[:2] == ["uv", "run"]:
            assert words[2] in dev, f"{words[2]} is no dev dependency"
            assert all((ROOT / w).exists() for w in words[3:] if w.startswith("tests/"))
        elif words and words[0] == "export":
            variable = words[1].partition("=")[0]
            assert variable in (ROOT / "tests" / "conftest.py").read_text()


# ARCHITECTURE ---------------------------------------------------------------------------

ARCH = "docs/ARCHITECTURE.md"


@check(ARCH, "inbound:   SchemaModel, ObjectRef, Column, ColumnFacts, ColumnReference, TableHints,")
def _boundary(block: Block, _context: Context) -> None:
    body = block.text.split("outbound:")[0]
    names = re.findall(r"\b[A-Z]\w+", body.replace("inbound:", "").replace("errors:", ""))
    assert [name for name in names if not hasattr(platform, name)] == []


@check(
    ARCH,
    "SchemaFacts.from_source(source, *, table_codes)            # DDL text, a Path, or a sequence",
)
def _constructors(block: Block, _context: Context) -> None:
    for line in block.text.splitlines():
        call = line.partition("#")[0].strip()
        name, _, shown = call.partition("(")
        parameters = inspect.signature(getattr(SchemaFacts, name.split(".")[1])).parameters
        spelled = []
        for parameter in parameters.values():
            if parameter.kind is parameter.KEYWORD_ONLY and "*" not in spelled:
                spelled.append("*")
            default = "" if parameter.default is parameter.empty else f"={parameter.default}"
            spelled.append(parameter.name + default)
        assert shown.rstrip(")").split(", ") == spelled, line


check(ARCH, _WRITE_PK)(_refusal)


_LEVEL_1 = "INSERT INTO catalog.tb_country (…)                    → ERROR    Seed INSERT targets catalog schema but should target prep_seed"


@check(ARCH, _LEVEL_1)
def _level_1(block: Block, context: Context) -> None:
    """Each seed, judged at level 1: the severity and the message the document shows."""
    unsuffixed = _facts(TRINITY.replace("fk_continent_id UUID", "fk_continent UUID"), CODES)
    row = {"id": "02030405-5001-8001-8000-000000000001", "identifier": "x", "iso_code": "FR"}
    for line, table in zip(block.text.splitlines(), ["catalog", "prep_seed"], strict=True):
        directory = context.tmp_path / table
        directory.mkdir()
        seeds.write(
            directory / "001.sql",
            f"{table}.tb_country",
            [{**row, "fk_continent": row["id"]}],
            facts=unsuffixed,
            mode="prep-seed",
        )
        report = platform.validate_seeds(directory, schema_dir=directory, max_level=1)
        shown = re.search(r"→ (\w+)\s+(.+)$", line)
        assert shown is not None, line
        severity, message = shown.groups()
        assert (severity, message) in {(v.severity.name, v.message) for v in report.violations}


_SQL_SITE = "SELECT pg_advisory_xact_lock(%s, %s)"


@check(ARCH, _SQL_SITE)
def _one_sql_site(block: Block, _context: Context) -> None:
    """Every statement readback.py runs, in the order it defines them: none left out."""
    names = {
        "{pk}": "<surrogate_pk>",
        "{id}": "<natural_id>",
        "{by}": "<identifier>",
        "{schema}": "<schema>",
        "{table}": "<table>",
        "{path}": "<path>",
        "{tables}": "<tables>",
        "{target_schema}": "<target_schema>",
        "{target}": "<target>",
        "{joined}": "<t.key = r.key>",
        "{own}": "<own_natural_id>",
        "{restarts}": "ALTER COLUMN <identity> RESTART, …",
    }
    shapes = []
    for template in (v for v in vars(readback).values() if isinstance(v, sql.Composable)):
        text = template.as_string()
        for placeholder, name in names.items():
            text = text.replace(placeholder, name)
        shapes.append(" ".join(text.split()))
    shown = [" ".join(part.split()) for part in block.text.split("\n\n")]
    assert shown == shapes


check(ARCH, "- name: catalog.tb_location")(_hierarchy)


_WRITERS = "# NOT NULL, no default, simply left out of `columns`  → accepted"


@check(ARCH, _WRITERS)
def _writers_accept(block: Block, context: Context) -> None:
    """Each call the document shows is accepted, and each value written as it shows."""
    call = next(line for line in block.text.splitlines() if line.startswith("write_copy_seed"))
    names = {
        "write_copy_seed": platform.write_copy_seed,
        "p": context.tmp_path / "left_out.sql",
        "rows": [{"id": "02030405-5001-8001-8000-000000000001", "fk_continent": 1}],
        "m": platform.parse_schema(TRINITY),
    }
    eval(call.partition("#")[0], names)
    model = platform.parse_schema(
        "CREATE SCHEMA s; CREATE TABLE s.t (pk BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,"
        " id UUID NOT NULL, n INTEGER, code VARCHAR(3), label TEXT NOT NULL);"
    )
    column = {"uuid": "id", "integer": "n", "varchar(3)": "code", "a NOT NULL": "label"}
    shown = re.findall(r"^(\S+)\s+into (.+?)\s+→ (\S+)$", block.text, re.M)
    assert len(shown) == len(column)
    for value, kind, written in shown:
        path = context.tmp_path / f"{column[kind]}.sql"
        platform.write_copy_seed(
            path, "s.t", [column[kind]], [{column[kind]: eval(value)}], model=model
        )
        assert path.read_text().splitlines()[1] == written, kind


@check(ARCH, "tables:")
def _trusts_trigger(block: Block, context: Context) -> None:
    ddl = WORKED.replace(
        "created_by UUID NOT NULL,", "created_by UUID NOT NULL,\n    updated_by UUID NOT NULL,"
    )
    manager = ScenarioManager(_facts(ddl, CODES))
    listed = yaml.safe_load(block.text)["tables"]
    parent = "  - name: catalog.tb_continent\n    count: 1\n"
    assert listed[0]["name"] == "catalog.tb_country"
    _checked(manager, _scenario_file(context.tmp_path, parent + block.text.partition("\n")[2]))


def _seed(url: str, tmp_path: Path, name: str) -> Path:
    """Two continents, written once as read-back writes them, into *url*'s own schema."""
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(CONTINENT)
    facts = _facts(CONTINENT, {"catalog.tb_continent": CODES["catalog.tb_continent"]})
    rows = fraiseql_semis.FakeDataGenerator(facts, 0x5001, seed=42).generate_rows(
        "catalog.tb_continent", count=2
    )
    return seeds.write(
        tmp_path / name, "catalog.tb_continent", rows, facts=facts, mode="read-back"
    ).path


_IDENTITIES = "fresh database         -> fk targets: [1, 2]"


@check(ARCH, _IDENTITIES, database=True)
def _identities(block: Block, context: Context) -> None:
    url = context.database()
    path = _seed(url, context.tmp_path, "rb.sql")
    found = []
    for label in ("fresh", "again"):
        if label == "again":
            with psycopg.connect(url, autocommit=True) as connection:
                connection.execute("TRUNCATE catalog.tb_continent")
        seeds.apply(url, [path])
        with psycopg.connect(url) as connection:
            keys = [
                pk
                for (pk,) in connection.execute(
                    "SELECT pk_continent FROM catalog.tb_continent ORDER BY 1"
                )
            ]
        found.append(str(keys))
    shown = [line.split(":", 1)[1].strip() for line in block.text.splitlines()]
    assert found == shown


_REPLAY = "SeedError: Failed to execute seed file rb.sql: duplicate key value violates unique"


@check(ARCH, _REPLAY, database=True)
def _replay(block: Block, context: Context) -> None:
    url = context.database()
    path = _seed(url, context.tmp_path, "rb.sql")
    seeds.apply(url, [path])
    with pytest.raises(platform.SeedError) as refused:
        seeds.apply(url, [path])
    shown = " ".join(block.text.removeprefix("SeedError: ").split())
    assert shown in " ".join(str(refused.value).split())


_OUTSIDE_FK = (
    "scenario minimal_seed: 3 rows of catalog.tb_city point at its rows of "
    "catalog.tb_country, by tb_city_fk_country_fkey"
)
pinned(
    ARCH,
    _OUTSIDE_FK,
    "tests/integration/test_cli_reset.py::"
    "test_a_row_outside_the_run_pointing_at_a_scenario_row_blocks_the_reset",
)
pinned(
    ARCH,
    "to_json() same source, twice     → identical      ✓",
    "tests/contract/test_model_json_is_not_cross_source_stable.py::test_to_json_differs_between_ddl_and_live",
)
pinned(
    ARCH,
    "default:  \"'draft'\"                  →  \"CAST('draft' AS catalog.status)\"",
    "tests/contract/test_model_json_is_not_cross_source_stable.py::test_the_facts_semis_digests_are_spelled_by_source",
)


_PIN_FILE = "// scenarios/minimal_seed.pin.json"


@check(ARCH, _PIN_FILE)
def _pin(block: Block, context: Context) -> None:
    """semis pin writes a file of these keys, in this order, where the comment says; the
    digest, version and date are the schema's, and the facts begin with the first table."""
    path = context.tmp_path / "scenarios" / "minimal_seed.yaml"
    path.parent.mkdir()
    shutil.copy(ROOT / "scenarios" / "minimal_seed.yaml", path)
    manager = ScenarioManager(_facts(WORKED, CODES))
    change = manager.pin(manager.load(path), path)
    assert change.path.relative_to(context.tmp_path).as_posix() == _PIN_FILE.removeprefix("// ")
    written = json.loads(change.path.read_text())
    assert re.findall(r'^  "(\w+)":', block.text, re.MULTILINE) == list(written)
    assert f'"source": "{written["source"]}"' in block.text
    assert f'"table": "{written["facts"][0]["table"]}"' in block.text
    assert written["digest"].startswith("sha256:")


@check(ARCH, "SemisError(ConfiturError-shaped: message, error_code, exit_code, resolution_hint)")
def _errors(block: Block, _context: Context) -> None:
    root = errors.SemisError("x", resolution_hint="y")
    assert all(hasattr(root, a) for a in ("error_code", "exit_code", "resolution_hint"))
    for name in re.findall(r"── (\w+)", block.text):
        assert issubclass(getattr(errors, name), errors.SemisError), name


@check(ARCH, "n = int.from_bytes(b, 'big')")
def _decoder(block: Block, _context: Context) -> None:
    value = uuid.UUID("01020304-5001-8001-8000-000000000042")
    names: dict[str, object] = {"b": value.bytes}
    exec(block.text, names)
    decoded = SemanticUUIDGenerator(0x5001).decode(value)
    fields = ("table_code", "scenario_id", "version", "sequence")
    assert [names[f] for f in fields] == [getattr(decoded, f) for f in fields]


def _illustration(text: str) -> None:
    """The UUID an illustration shows decodes to the numbers it labels."""
    shown = re.search(r"([0-9a-f]{8})-([0-9a-f]{4})-([0-9a-f]{4})-[0-9a-f-]+", text)
    assert shown is not None
    decoded = SemanticUUIDGenerator(0x5001).decode(uuid.UUID(shown.group(0)))
    assert decoded.table_code == int(shown.group(1), 16)
    assert f"{decoded.scenario_id:x}" in text and f"v{decoded.version}" in text
    assert f"{decoded.sequence:#x} = {decoded.sequence}" in text


check(ARCH, "01020304-5001-8001-8000-000000000042")(lambda block, _c: _illustration(block.text))


# PRD ------------------------------------------------------------------------------------


@check("docs/PRD.md", "CREATE SCHEMA catalog;")
def _trinity(block: Block, _context: Context) -> None:
    facts = _facts(block.text, CODES)
    country = facts.facts_for("catalog.tb_country")
    assert (country.surrogate_pk, country.natural_id) == ("pk_country", "id")
    key = next(c for c in country.columns if c.name == "fk_continent").foreign_key
    assert key is not None
    assert (key.table.display, key.column) == ("catalog.tb_continent", "pk_continent")
    assert "pk_country" not in [c.name for c in country.columns]


check("docs/PRD.md", "┌────────────┬──────────┬───┬──────────┬────┬──────────────────────┐")(
    lambda block, _c: _illustration(block.text)
)


@check("docs/PRD.md", ">>> from uuid import UUID")
def _prd_decode(block: Block, _context: Context) -> None:
    _doctest(block, {})


# The site -----------------------------------------------------------------------------


def _page(name: str) -> str:
    return str(PAGES / name)


covered(_page("index.mdx"), _INSTALL, by=("README.md", _INSTALL))


@dataclass
class _Reader:
    """A page's commands, run as a reader runs them: in a directory and a database of
    their own, each output kept for the block that shows it.

    A command may fail only where the page shows its refusal: the block after it."""

    context: Context
    database: str | None
    output: str | None = None
    query: str | None = None
    refused: bool = False

    @cached_property
    def url(self) -> str:
        assert self.database is not None, "this page names no database"
        return self.context.database()

    def run(self, line: str) -> None:
        for words in _commands_of(line):
            assert not self.refused, f"the refusal before {line!r} is not shown"
            while "=" in words[0]:
                self._assign(*words.pop(0).split("=", 1))
            getattr(self, "_" + words[0].replace("-", "_"))(*words[1:])

    def _assign(self, variable: str, value: str) -> None:
        """``PYTHONPATH=.`` is the one assignment a page makes: what it puts on the path."""
        assert variable == "PYTHONPATH"
        self.context.monkeypatch.syspath_prepend(str(Path(value).resolve()))

    def done(self) -> None:
        assert not self.refused, "the page's last command was refused, and no block shows it"

    def _mkdir(self, path: str) -> None:
        Path(path).mkdir()

    def _cd(self, path: str) -> None:
        self.context.monkeypatch.chdir(path)

    def _uv(self, command: str, *arguments: str) -> None:
        if command == "add":
            assert list(arguments) == [PYPROJECT["project"]["name"]]
        else:
            subprocess.run(["uv", command, *arguments], check=True, capture_output=True)

    def _source(self, path: str) -> None:
        assert path == ".venv/bin/activate", "uv makes its environment in .venv"

    def _createdb(self, name: str) -> None:
        assert name == self.database

    def _psql(self, *arguments: str) -> None:
        options = dict(zip(arguments[::2], arguments[1::2], strict=True))
        assert options.pop("-d") == self.database
        with psycopg.connect(self.url, autocommit=True) as connection:
            for flag, value in zip(arguments[::2], arguments[1::2], strict=True):
                if flag == "-f":
                    connection.execute(_page_sql(Path(value).read_text()))
        self.query = options.get("-c")

    def _head(self, flag: str, count: str, path: str) -> None:
        assert flag == "-n"
        self.output = "\n".join(Path(path).read_text().splitlines()[: int(count)])

    def _cp(self, source: str, target: str) -> None:
        shutil.copy(source, target)

    def _cat(self, source: str, append: str, target: str) -> None:
        assert append == ">>"
        with Path(target).open("a") as file:
            file.write(Path(source).read_text())

    def _diff(self, flag: str, left: str, right: str) -> None:
        assert flag == "-r"
        files = {path.relative_to(left) for path in Path(left).rglob("*")}
        assert files == {path.relative_to(right) for path in Path(right).rglob("*")}
        for name in files:
            if (Path(left) / name).is_file():
                assert (Path(left) / name).read_bytes() == (Path(right) / name).read_bytes()

    def _semis(self, *arguments: str) -> None:
        given = [self.url if a == f"postgresql:///{self.database}" else a for a in arguments]
        result = CliRunner().invoke(app, given)
        assert result.exit_code in {0, 1}, result.output
        self.output, self.refused = result.output, result.exit_code == 1

    def shows(self, block: Block) -> None:
        """*block* is what the last command printed."""
        if self.query is not None:
            columns, rows = _psql(block.text)
            with psycopg.connect(self.url) as connection:
                cursor = connection.execute(_page_sql(self.query))
                found = [tuple(str(value) for value in row) for row in cursor.fetchall()]
                assert [column.name for column in cursor.description or ()] == columns
            assert found == rows
        else:
            assert self.output is not None, f"no command printed {block.key[1]}"
            printed = [line.rstrip() for line in self.output.strip("\n").splitlines()]
            assert printed == block.text.splitlines()
        self.output = self.query = None
        self.refused = False


def _page_sql(text: str) -> LiteralString:
    """SQL a page gives the reader to run, run as given: the page is the test's input."""
    return cast("LiteralString", text)


def _commands_of(line: str) -> list[list[str]]:
    """The commands of one shell line, ``&&`` between them."""
    commands: list[list[str]] = [[]]
    for word in shlex.split(line, comments=True):
        if word == "&&":
            commands.append([])
        else:
            commands[-1].append(word)
    return [command for command in commands if command]


def _quoted(block: Block) -> bool:
    """Whether *block* repeats another document's, checked there: no step of a walk."""
    by = CHECKS.get(block.key)
    return isinstance(by, tuple) and by[0] != block.document


def _write(block: Block) -> None:
    """A titled block, written where its title says."""
    assert block.title is not None
    Path(block.title).parent.mkdir(parents=True, exist_ok=True)
    Path(block.title).write_text(block.text + "\n")


def _walk(document: str, reader: _Reader) -> None:
    """*document* read top to bottom: each titled block written, each command run, and
    each other block what the command before it printed."""
    for shown in (b for b in BLOCKS if b.document == document and not _quoted(b)):
        if shown.title is not None:
            _write(shown)
        elif shown.language == "bash":
            for line in shown.text.splitlines():
                reader.run(line)
        else:
            reader.shows(shown)
    reader.done()


GETTING_STARTED = _page("getting-started.md")


def _walked(document: str, database: str | None = None, *, project: str | None = None) -> None:
    """Every block of *document* that quotes no other checked by one walk, registered on
    the first. *project* is a page whose files the reader has written first."""

    def read(_block: Block, context: Context) -> None:
        context.monkeypatch.chdir(context.tmp_path)
        for block in BLOCKS:
            if block.document == project and block.title is not None:
                _write(block)
        _walk(document, _Reader(context, database))

    first, *rest = [b for b in BLOCKS if b.document == document and not _quoted(b)]
    check(document, first.key[1], database=database is not None)(read)
    for block in rest:
        covered(document, block.key[1], by=first.key)


def _quotes(document: str, *keys: tuple[str, str]) -> None:
    """Blocks *document* repeats from other documents, each covered by its own check."""
    for key in keys:
        covered(document, key[1], by=key)


_walked(GETTING_STARTED, "continents_dev")

_DDL = ("docs/PRD.md", "CREATE SCHEMA catalog;")
_DIAGRAM = ("docs/PRD.md", "┌────────────┬──────────┬───┬──────────┬────┬──────────────────────┐")
_quotes(_page("concepts/trinity-pattern.md"), _DDL, ("README.md", _WRITE_PK))
_quotes(_page("concepts/semantic-uuid.md"), _DIAGRAM, ("docs/PRD.md", ">>> from uuid import UUID"))
_walked(_page("concepts/semantic-uuid.md"), project=GETTING_STARTED)
_quotes(
    _page("concepts/fk-modes.md"),
    ("README.md", "# scenarios/minimal_seed.yaml"),
    (ARCH, _LEVEL_1),
    (ARCH, _SQL_SITE),
    ("README.md", "- name: inventory.tb_item"),
    ("README.md", "existing:"),
)
_quotes(_page("concepts/row-contract.md"), (ARCH, _WRITERS))
_walked(_page("concepts/row-contract.md"))
_quotes(
    _page("concepts/determinism.md"),
    (ARCH, _IDENTITIES),
    ("README.md", _AGAIN),
    ("README.md", _REFUSED),
    ("README.md", _RESET),
    (ARCH, _OUTSIDE_FK),
)
_walked(_page("concepts/determinism.md"), project=GETTING_STARTED)
_quotes(_page("concepts/schema-pins.md"), (ARCH, _PIN_FILE))
_walked(_page("concepts/schema-pins.md"), project=GETTING_STARTED)

_quotes(
    _page("guides/scenarios.md"),
    ("README.md", "- name: inventory.tb_item"),
    ("README.md", "- name: catalog.tb_currency"),
    ("README.md", "- name: catalog.tb_location"),
    ("README.md", "- name: catalog.tb_location        # a flat set: no row has a parent"),
    ("README.md", "- name: shop.tb_customer"),
    ("README.md", "existing:"),
    ("README.md", "- name: tenant.tb_organization"),
)
_walked(_page("guides/scenarios.md"), project=GETTING_STARTED)
_quotes(_page("guides/provider-libraries.md"), ("README.md", _ACME), ("README.md", _ENTRY))


@check(_page("guides/provider-libraries.md"), "providers: [i18n, acme]")
def _enabled(block: Block, context: Context) -> None:
    """Installed, the README's library is enabled by the line the page gives."""
    _acme_installed(context)
    _project(context.tmp_path)
    with (context.tmp_path / "semis.yaml").open("a") as project:
        project.write(block.text + "\n")
    libraries = Project.load(context.tmp_path / "semis.yaml").libraries
    assert [library.name for library in libraries] == ["i18n", "acme"]


_walked(_page("guides/validating-seeds.md"), "continents_check", project=GETTING_STARTED)

CI = _page("guides/ci.md")


def _in_getting_started(context: Context) -> None:
    context.monkeypatch.chdir(context.tmp_path)
    for block in BLOCKS:
        if block.document == GETTING_STARTED and block.title is not None:
            _write(block)


@check(CI, "name: Seeds")
def _workflow(block: Block, context: Context) -> None:
    """The workflow loads; its actions are the repository's own CI's, each tag naming the
    release CI pins by commit; its database the newest PostgreSQL that CI runs; each
    command it runs is one the project gives, and each semis line parses."""
    _in_getting_started(context)
    workflow = yaml.safe_load(block.text)
    (job,) = workflow["jobs"].values()
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    ours = yaml.safe_load(text)
    released = dict(re.findall(r"uses: ([\w.-]+/[\w.-]+)@[0-9a-f]{40} # (v[\d.]+)", text))
    for action, _, tag in (step["uses"].partition("@") for step in job["steps"] if "uses" in step):
        assert released[action] == tag or released[action].startswith(f"{tag}."), action
    postgres = job["services"]["postgres"]
    newest = ours["jobs"]["integration"]["strategy"]["matrix"]["postgresql"][-1]
    assert postgres["image"] == f"postgres:{newest}"
    database = postgres["env"]["POSTGRES_DB"]
    assert job["env"]["CONFITURE_DATABASE_URL"].endswith(f"/{database}")
    files = {b.title for b in BLOCKS if b.title} | {"db/schema/030_resolvers.sql"}
    for line in (line for step in job["steps"] for line in step.get("run", "").splitlines()):
        words = shlex.split(line)
        if words[:2] == ["uv", "run"]:
            _parses(words[2:])
        elif words[0] == "psql":
            assert set(words[2::2]) == {"-f"} and set(words[3::2]) <= files, line
        else:
            assert words[:2] in (["uv", "sync"], ["git", "diff"]), line


_quotes(_page("reference/semis-yaml.md"), ("README.md", "# semis.yaml"))
_quotes(
    _page("reference/scenario-file.md"),
    ("README.md", "# scenarios/minimal_seed.yaml"),
    ("README.md", "existing:"),
)

# fraiseql/semis#1's table: an audit column and a soft-delete column, both nullable.
SHOP = """CREATE SCHEMA shop;
CREATE TABLE shop.tb_customer (pk_customer bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE, identifier text NOT NULL UNIQUE,
  name text NOT NULL, deleted_at timestamptz, created_by uuid);"""


@check(
    _page("reference/scenario-file.md"),
    "shop.tb_customer leaves deleted_at, created_by NULL; fill: draws them",
)
def _left_null(block: Block, context: Context) -> None:
    manager = ScenarioManager(_facts(SHOP, {"shop.tb_customer": 0x0B}))
    tables = "  - name: shop.tb_customer\n    count: 3\n"
    path = _scenario_file(context.tmp_path, tables, mode="read-back")
    assert manager.check(manager.load(path), no_pin=True)[1:] == (block.text,)


# The same table with a nullable key to a segment the run does not generate.
SEGMENTED = SHOP.replace(
    "CREATE TABLE shop.tb_customer",
    "CREATE TABLE shop.tb_segment (pk_segment bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY);\n"
    "CREATE TABLE shop.tb_customer",
).replace(
    "name text NOT NULL,", "name text NOT NULL,\n  fk_segment bigint REFERENCES shop.tb_segment,"
)


@check(
    _page("reference/scenario-file.md"),
    "shop.tb_customer leaves fk_segment, deleted_at NULL; fill: draws the values, "
    "a parent under tables: or existing: points the keys",
)
def _key_left_null(block: Block, context: Context) -> None:
    manager = ScenarioManager(_facts(SEGMENTED, {"shop.tb_customer": 0x0B}))
    tables = "  - name: shop.tb_customer\n    count: 3\n    fill: [created_by]\n"
    path = _scenario_file(context.tmp_path, tables, mode="read-back")
    assert manager.check(manager.load(path), no_pin=True)[1:] == (block.text,)


@check(_page("reference/scenario-file.md"), "copies:")
def _copies_shape(block: Block, context: Context) -> None:
    """The fragment loads as one table's copies, the key and parent column apart."""
    tables = "  - name: catalog.tb_country\n    count: 1\n" + textwrap.indent(block.text, "    ")
    path = _scenario_file(context.tmp_path, tables)
    (spec,) = ScenarioManager(_facts(TRINITY, CODES)).load(path).tables
    assert spec.copies == {"tenant_id": ("fk_customer_org", "id")}


TENANTS = """CREATE SCHEMA tenant;
CREATE TABLE tenant.tb_organization (pk_organization bigint GENERATED ALWAYS AS IDENTITY
  PRIMARY KEY, id uuid NOT NULL UNIQUE, identifier text NOT NULL UNIQUE, name text NOT NULL);
CREATE TABLE tenant.tb_contact (pk_contact bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id uuid NOT NULL UNIQUE, identifier text NOT NULL UNIQUE, tenant_id uuid NOT NULL,
  fk_customer_org bigint NOT NULL REFERENCES tenant.tb_organization);"""


@check(_page("reference/cli.md"), "deleted 6 rows from shop.tb_order; identity restarted")
def _reset_report(block: Block, _context: Context) -> None:
    """The lines semis reset prints for a table it empties and one that keeps rows."""
    lines = [
        _deleted(Deleted("shop.tb_order", 6, 0, restarted=True)),
        _deleted(Deleted("shop.tb_customer", 2, 3, restarted=False)),
        "committed",
    ]
    assert lines == block.text.splitlines()


@check("README.md", "- name: tenant.tb_organization")
def _copied(block: Block, context: Context) -> None:
    """What the page says: twelve contacts over six organizations, each holding its own's
    id. In prep-seed, a key carries its parent's id, so the two columns agree."""
    facts = _facts(TENANTS, {"tenant.tb_organization": 0x0C, "tenant.tb_contact": 0x0D})
    manager = ScenarioManager(facts)
    path = _scenario_file(context.tmp_path, textwrap.indent(block.text, "  "), mode="read-back")
    scenario = manager.load(path)
    manager.check(scenario, no_pin=True)
    resolver = PrepSeedResolver()
    walk = FakeDataGenerator(facts, scenario.id, seed=42).walk(
        {spec.name: spec.count for spec in scenario.tables},
        copies={spec.name: spec.copies for spec in scenario.tables},
        resolver=resolver,
    )
    drawn: dict[str, list[dict[str, object]]] = {}
    for table, stream in walk:
        drawn[table.ref.display] = rows = list(stream)
        resolver.remember(table, rows)
    organizations = [row["id"] for row in drawn["tenant.tb_organization"]]
    contacts = drawn["tenant.tb_contact"]
    assert [row["tenant_id"] for row in contacts] == organizations * 2
    assert [row["fk_customer_org"] for row in contacts] == organizations * 2


@check("README.md", "- name: shop.tb_customer")
def _filled(block: Block, context: Context) -> None:
    """What the comment says: the column fill: names is drawn, the other left NULL."""
    manager = ScenarioManager(_facts(SHOP, {"shop.tb_customer": 0x0B}))
    path = _scenario_file(context.tmp_path, textwrap.indent(block.text, "  "), mode="read-back")
    assert manager.check(manager.load(path), no_pin=True)[1:] == (
        "shop.tb_customer leaves deleted_at NULL; fill: draws them",
    )


API = _page("reference/python-api.md")
_quotes(
    API,
    ("README.md", "from pathlib import Path"),
    (
        ARCH,
        "SchemaFacts.from_source(source, *, table_codes)            # DDL text, a Path, or a sequence",
    ),
    ("README.md", "from fraiseql_semis import ScenarioManager"),
    ("docs/PRD.md", ">>> from uuid import UUID"),
)


def _spelled(target: Callable[..., object]) -> str:
    """*target*'s parameters as Python spells a call's: ``a, *, b=None``."""
    spelled: list[str] = []
    for parameter in inspect.signature(target).parameters.values():
        if parameter.name == "self":
            continue
        if parameter.kind is parameter.KEYWORD_ONLY and "*" not in spelled:
            spelled.append("*")
        default = "" if parameter.default is parameter.empty else f"={parameter.default!r}"
        spelled.append(parameter.name + default)
    return ", ".join(spelled)


def _signatures(block: Block, _context: Context) -> None:
    """Each line is a name fraiseql_semis exports, called with its own parameters."""
    for line in block.text.splitlines():
        name, _, shown = line.partition("(")
        target: object = fraiseql_semis
        for part in name.split("."):
            target = getattr(target, part)
        assert callable(target), name
        assert shown.removesuffix(")") == _spelled(target), line


for _first in (
    "TableCodes(mapping)",
    "FakeDataGenerator(facts, scenario_id, *, seed=None, locale='en_US', providers=None, identifier_column='identifier')",
    "seeds.write(path, table, rows, *, facts, mode, format=None)",
    "ScenarioManager(facts, *, providers=None, libraries=(), staging=None)",
    "Scenario(id, name, mode, tables, locale='en_US', seed=None, description='', pin=None, existing=())",
    "Project.load(path)",
    "Library(name, providers, rules=())",
):
    check(API, _first)(_signatures)


@check(CI, "uv run semis apply scenarios/countries.yaml --dry-run")
def _dry_run(block: Block, context: Context) -> None:
    _in_getting_started(context)
    _parses(shlex.split(block.text)[2:])


# The accounting ---------------------------------------------------------------------------


def _ambiguous(
    blocks: list[Block], checks: Mapping[tuple[str, str], object]
) -> list[tuple[str, str]]:
    """The first lines two blocks of one document share, where no check tells them apart.

    A block is found by its first line, so two that open alike are one key. Only a check
    of their own document, which reads its blocks in order, can tell them apart: such
    blocks are covered by it.
    """
    keys = [block.key for block in blocks]
    shared = sorted({key for key in keys if keys.count(key) > 1})
    return [
        key for key in shared if not (isinstance(by := checks.get(key), tuple) and by[0] == key[0])
    ]


def test_every_block_has_one_check() -> None:
    assert _ambiguous(BLOCKS, CHECKS) == [], "two blocks of one document open alike"
    assert sorted({block.key for block in BLOCKS} - set(CHECKS)) == []


def test_every_check_has_its_block() -> None:
    keys = {block.key for block in BLOCKS}
    assert sorted(set(CHECKS) - keys) == []
    assert all(by in keys for by in CHECKS.values() if isinstance(by, tuple))


def _unrepeated(
    blocks: list[Block], checks: Mapping[tuple[str, str], Check | tuple[str, str]]
) -> list[tuple[str, str]]:
    """The blocks covered by another document's check that do not repeat its block.

    Within one document the covering check reads the block it covers; across two, it
    reads only its own, so the covered block must be that one, verbatim.
    """
    text = {block.key: block.text for block in blocks}
    return [
        key
        for key, by in checks.items()
        if isinstance(by, tuple) and by[0] != key[0] and text.get(key) != text.get(by)
    ]


def test_a_block_covered_from_another_document_repeats_it() -> None:
    assert _unrepeated(BLOCKS, CHECKS) == []


@pytest.mark.parametrize(
    "block",
    [
        pytest.param(
            b,
            id=f"{b.document}:{b.line}",
            marks=[pytest.mark.integration] if b.key in NEEDS_DATABASE else [],
        )
        for b in BLOCKS
        if b.key in CHECKS and not isinstance(CHECKS[b.key], tuple)
    ],
)
def test_the_example_is_true(
    block: Block, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    run = CHECKS[block.key]
    assert callable(run)
    run(block, Context(tmp_path, monkeypatch, request))


def test_a_pin_taken_as_the_pins_page_says_lists_and_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """The page's pinning, followed literally in the getting-started project: the block
    pasted into the scenario, the facts copied beside it; the scenarios directory still
    lists, and a re-run matches its pin."""
    monkeypatch.chdir(tmp_path)
    for block in BLOCKS:
        if block.document == GETTING_STARTED and block.title is not None:
            _write(block)
    reader = _Reader(Context(tmp_path, monkeypatch, request), None)
    pages = (b for b in BLOCKS if b.document == _page("concepts/schema-pins.md"))
    pinning = next(b for b in pages if b.language == "bash")
    for line in pinning.text.splitlines():
        reader.run(line)
    listed = CliRunner().invoke(app, ["list-scenarios"])
    again = CliRunner().invoke(app, ["seeds", "scenarios/continents.yaml", "-o", "db/again"])
    assert (listed.exit_code, again.exit_code) == (0, 0), listed.output + again.output
    assert "continents" in [line.split()[2] for line in listed.output.splitlines()]
    assert again.output.startswith("scenario continents matches its schema pin (ddl sha256:")
