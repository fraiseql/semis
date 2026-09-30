"""Every example in README, ARCHITECTURE and PRD is true of the code that ships.

Each fenced block is found by its document and its first line, and checked: Python is
run, YAML is loaded as a scenario or a project, a command is parsed by the command it
names, and an output is compared with what shipped code produces. A confiture behaviour
ARCHITECTURE shows is pinned by a contract test the document names. A block no check
covers fails, and so does a check whose block is gone.
"""

import doctest
import inspect
import re
import shlex
import textwrap
import tomllib
import uuid
from collections.abc import Callable, Iterator
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import psycopg
import pytest
import yaml
from confiture import platform
from psycopg import sql
from psycopg.conninfo import make_conninfo
from typer.main import get_group
from typer.testing import CliRunner

import fraiseql_semis
from fraiseql_semis import (
    Library,
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
from fraiseql_semis.project import ENTRY_POINTS
from fraiseql_semis.providers import SHIPPED
from tests.ddl import CODES, HIERARCHY, HIERARCHY_CODES, TRINITY, WORKED

ROOT = Path(__file__).parents[2]
DOCUMENTS = ("README.md", "docs/ARCHITECTURE.md", "docs/PRD.md")
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())


@dataclass(frozen=True)
class Block:
    document: str
    line: int
    language: str
    text: str

    @property
    def key(self) -> tuple[str, str]:
        return self.document, self.text.splitlines()[0].strip()


def _blocks(document: str) -> Iterator[Block]:
    lines = (ROOT / document).read_text().splitlines()
    index = 0
    while index < len(lines):
        opening = re.fullmatch(r"(\s*)```(\w*)", lines[index])
        if opening:
            end = next(i for i in range(index + 1, len(lines)) if lines[i].strip() == "```")
            text = textwrap.dedent("\n".join(lines[index + 1 : end]))
            yield Block(document, index + 1, opening.group(2), text)
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
        """A database of its own, dropped when the test ends: examples name `catalog`."""
        url: str = self.request.getfixturevalue("database_url")
        name = f"semis_docs_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))

        def drop() -> None:
            with psycopg.connect(url, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))

        self.request.addfinalizer(drop)
        return make_conninfo(url, dbname=name)


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


def _doctest(block: Block, globs: dict[str, object]) -> None:
    parser = doctest.DocTestParser()
    test = parser.get_doctest(block.text, globs, block.key[1], block.document, block.line)
    runner = doctest.DocTestRunner(optionflags=doctest.NORMALIZE_WHITESPACE)
    out = StringIO()
    runner.run(test, out=out.write)
    assert runner.failures == 0, out.getvalue()


# README ---------------------------------------------------------------------------------


@check("README.md", "uv add fraiseql-semis          # brings fraiseql-confiture>=1.27,<2")
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
    header, _rule, *rows, _count = shown.text.splitlines()
    columns = [name.strip() for name in header.split("|")]
    expected = [tuple(value.strip() for value in row.split("|")) for row in rows]
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
    rows = [{"pk_continent": 1, "id": "02030405-5001-0001-0000-000000000001"}]
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


@check("README.md", "$ semis decode-uuid 02030405-5001-0001-0000-000000000001")
def _decode(block: Block, context: Context) -> None:
    command, *shown = block.text.splitlines()
    _project(context.tmp_path)
    context.monkeypatch.chdir(context.tmp_path)
    result = CliRunner().invoke(app, shlex.split(command)[2:])
    assert result.exit_code == 0, result.output
    assert [line.rstrip() for line in result.output.splitlines()] == shown


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
    group = get_group(app)
    for line in block.text.splitlines():
        program, name, *arguments = shlex.split(line, comments=True)
        assert program == "semis"
        command = group.commands.get(name)
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
def _library(block: Block, context: Context) -> None:
    """The two blocks as a package: installed, it is enabled by its short name."""
    entry = next(b for b in BLOCKS if b.key == ("README.md", _ENTRY))
    site = context.tmp_path / "site"
    (site / "acme_semis").mkdir(parents=True)
    (site / "acme_semis" / "__init__.py").write_text(block.text)
    info = site / "acme_semis-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: acme-semis\nVersion: 1.0\n")
    points = tomllib.loads(entry.text)["project"]["entry-points"][ENTRY_POINTS]
    (info / "entry_points.txt").write_text(
        f"[{ENTRY_POINTS}]\n" + "".join(f"{k} = {v}\n" for k, v in points.items())
    )
    context.monkeypatch.syspath_prepend(str(site))
    _project(context.tmp_path)
    with (context.tmp_path / "semis.yaml").open("a") as project:
        project.write("providers: [i18n, acme]\n")
    library = Project.load(context.tmp_path / "semis.yaml").libraries[1]
    assert isinstance(library, Library)
    assert (library.name, sorted(library.providers)) == ("acme", ["ticker"])


covered("README.md", _ENTRY, by=("README.md", _ACME))


def _hierarchy(block: Block, context: Context) -> None:
    manager = ScenarioManager(_facts(HIERARCHY, HIERARCHY_CODES))
    tables = textwrap.indent(block.text, "  ")
    _checked(manager, _scenario_file(context.tmp_path, tables, mode="read-back"))


check("README.md", "- name: catalog.tb_location")(_hierarchy)


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


@check(
    ARCH,
    "INSERT INTO catalog.tb_country (…)                    → ERROR    Seed INSERT targets catalog schema but should target prep_seed",
)
def _level_1(block: Block, context: Context) -> None:
    """Each seed, judged at level 1: the severity and the message the document shows."""
    unsuffixed = _facts(TRINITY.replace("fk_continent_id UUID", "fk_continent UUID"), CODES)
    row = {"id": "02030405-5001-0001-0000-000000000001", "identifier": "x", "iso_code": "FR"}
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


@check(
    ARCH, "SELECT <surrogate_pk>, <natural_id> FROM <schema>.<table> WHERE <natural_id> = ANY(%s)"
)
def _one_sql_site(block: Block, _context: Context) -> None:
    names = {
        "{pk}": "<surrogate_pk>",
        "{id}": "<natural_id>",
        "{schema}": "<schema>",
        "{table}": "<table>",
        "{path}": "<path>",
    }
    shapes = []
    for template in (readback._LEARN, readback._SET_PATHS):
        text = template.as_string()
        for placeholder, name in names.items():
            text = text.replace(placeholder, name)
        shapes.append(" ".join(text.split()))
    shown = [" ".join(part.split()) for part in block.text.split("\n\n")]
    assert shown == shapes


check(ARCH, "- name: catalog.tb_location")(_hierarchy)


@check(ARCH, "# NOT NULL, no default, simply left out of `columns`  → accepted")
def _writers_accept(block: Block, context: Context) -> None:
    """Each call the document shows is accepted, and each value written as it shows."""
    call = next(line for line in block.text.splitlines() if line.startswith("write_copy_seed"))
    names = {
        "write_copy_seed": platform.write_copy_seed,
        "p": context.tmp_path / "left_out.sql",
        "rows": [{"id": "02030405-5001-0001-0000-000000000001", "fk_continent": 1}],
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


@check(ARCH, "fresh database         -> fk targets: [1, 2]", database=True)
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


@check(
    ARCH,
    "SeedError: Failed to execute seed file rb.sql: duplicate key value violates unique",
    database=True,
)
def _replay(block: Block, context: Context) -> None:
    url = context.database()
    path = _seed(url, context.tmp_path, "rb.sql")
    seeds.apply(url, [path])
    with pytest.raises(platform.SeedError) as refused:
        seeds.apply(url, [path])
    shown = " ".join(block.text.removeprefix("SeedError: ").split())
    assert shown in " ".join(str(refused.value).split())


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


@check(ARCH, "schema_pin:")
def _pin(block: Block, context: Context) -> None:
    """A run writes a block of these keys; the digest, version and date are the run's."""
    manager = ScenarioManager(_facts(WORKED, CODES))
    run = manager.execute(manager.load(ROOT / "scenarios" / "minimal_seed.yaml"), context.tmp_path)
    written = yaml.safe_load(run.pin_path.read_text())["schema_pin"]
    shown = yaml.safe_load(block.text)["schema_pin"]
    assert list(written) == list(shown)
    assert (written["source"], written["snapshot"]) == (shown["source"], shown["snapshot"])
    assert written["digest"].startswith(shown["digest"].split(":")[0] + ":")


@check(ARCH, "SemisError(ConfiturError-shaped: message, error_code, exit_code, resolution_hint)")
def _errors(block: Block, _context: Context) -> None:
    root = errors.SemisError("x", resolution_hint="y")
    assert all(hasattr(root, a) for a in ("error_code", "exit_code", "resolution_hint"))
    for name in re.findall(r"── (\w+)", block.text):
        assert issubclass(getattr(errors, name), errors.SemisError), name


@check(ARCH, "table_code  = int.from_bytes(b[0:4],  'big')    # 32 bits")
def _decoder(block: Block, _context: Context) -> None:
    value = uuid.UUID("01020304-5001-0001-0000-000000000042")
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


check(ARCH, "01020304-5001-0001-0000-000000000042")(lambda block, _c: _illustration(block.text))


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


check("docs/PRD.md", "┌────────────┬──────────┬──────────┬────────────────────────┐")(
    lambda block, _c: _illustration(block.text)
)


@check("docs/PRD.md", ">>> from uuid import UUID")
def _prd_decode(block: Block, _context: Context) -> None:
    _doctest(block, {})


# The accounting ---------------------------------------------------------------------------


def test_every_block_has_one_check() -> None:
    keys = [block.key for block in BLOCKS]
    assert len(keys) == len(set(keys)), "two blocks of one document open alike"
    assert sorted(set(keys) - set(CHECKS)) == []


def test_every_check_has_its_block() -> None:
    keys = {block.key for block in BLOCKS}
    assert sorted(set(CHECKS) - keys) == []
    assert all(by in keys for by in CHECKS.values() if isinstance(by, tuple))


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
