"""The ``semis`` command: each subcommand delegates to the library and reports what it did."""

import re
from pathlib import Path

import pytest
import typer
from typer.core import TyperGroup
from typer.testing import CliRunner, Result

from fraiseql_semis.cli import app, main
from fraiseql_semis.cli.main import _masked, _where
from fraiseql_semis.errors import UnreachableDatabaseError
from fraiseql_semis.readback import checked_url
from tests.ddl import TRINITY

runner = CliRunner()

# Typer colours a usage error wherever it forces a terminal, as it does on GitHub Actions.
_STYLE = re.compile(r"\x1b\[[0-9;]*m")


def _usage(result: Result) -> str:
    """*result*'s output, its styling removed."""
    return _STYLE.sub("", result.output)


SEMIS_YAML = """\
schema:
  ddl: schema.sql
table_codes:
  catalog.tb_continent: 0x02030405
  catalog.tb_country: 0x03040506
"""
SCENARIO = """\
scenario_id: 0x5001
name: minimal_seed
mode: prep-seed
seed: 42
tables:
  - name: catalog.tb_country
    count: 4
  - name: catalog.tb_continent
    count: 2
"""


def _project(tmp_path: Path, **scenarios: str) -> Path:
    """A project directory: semis.yaml, the TRINITY schema, and *scenarios* by file stem."""
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "schema.sql").write_text(TRINITY)
    (tmp_path / "scenarios").mkdir()
    for stem, text in scenarios.items():
        (tmp_path / "scenarios" / f"{stem}.yaml").write_text(text)
    config = tmp_path / "semis.yaml"
    config.write_text(SEMIS_YAML)
    return config


def test_decode_uuid_prints_four_fields() -> None:
    result = runner.invoke(app, ["decode-uuid", "02030405-5001-8001-8000-000000000042"])
    assert (result.exit_code, result.output) == (
        0,
        "table_code   0x02030405\nscenario_id  0x5001\nversion      1\nsequence     66\n",
    )


def test_decode_uuid_refuses_a_uuid_semis_did_not_encode() -> None:
    result = runner.invoke(app, ["decode-uuid", "550e8400-e29b-41d4-a716-446655440000"])
    said = " ".join(_usage(result).replace("│", " ").split())
    assert (result.exit_code, "is a version 4 UUID, which semis did not encode" in said) == (
        2,
        True,
    )


def test_decode_uuid_names_the_table_and_the_scenario(tmp_path: Path) -> None:
    config = _project(tmp_path, minimal_seed=SCENARIO)
    uuid = "02030405-5001-8001-8000-000000000042"
    result = runner.invoke(app, ["decode-uuid", uuid, "--config", str(config)])
    assert result.output.splitlines()[:2] == [
        "table_code   0x02030405   catalog.tb_continent",
        "scenario_id  0x5001       minimal_seed",
    ]


def test_decode_uuid_says_what_it_cannot_name(tmp_path: Path) -> None:
    config = _project(tmp_path)
    uuid = "0a0b0c0d-7001-8001-8000-000000000001"
    result = runner.invoke(app, ["decode-uuid", uuid, "--config", str(config)])
    assert result.output.splitlines()[:2] == [
        "table_code   0x0a0b0c0d   no table has this code",
        "scenario_id  0x7001       no scenario file has this id",
    ]


def test_decode_uuid_reports_an_id_two_scenarios_use(tmp_path: Path) -> None:
    config = _project(
        tmp_path, minimal_seed=SCENARIO, copy=SCENARIO.replace("minimal_seed", "copy")
    )
    uuid = "02030405-5001-8001-8000-000000000042"
    result = runner.invoke(app, ["decode-uuid", uuid, "--config", str(config)])
    assert result.output.splitlines()[1] == (
        "scenario_id  0x5001       copy, minimal_seed (2 scenario files share this id)"
    )


def test_list_scenarios_prints_id_mode_name_and_file(tmp_path: Path) -> None:
    other = SCENARIO.replace("0x5001", "0x5002").replace("minimal_seed", "demo")
    config = _project(tmp_path, minimal_seed=SCENARIO, demo=other.replace("prep-seed", "read-back"))
    result = runner.invoke(app, ["list-scenarios", "--config", str(config)])
    assert (result.exit_code, result.output) == (
        0,
        "0x5001  prep-seed  minimal_seed  minimal_seed.yaml\n"
        "0x5002  read-back  demo          demo.yaml\n",
    )


def test_list_scenarios_refuses_an_id_two_scenarios_use(tmp_path: Path) -> None:
    config = _project(
        tmp_path, minimal_seed=SCENARIO, copy=SCENARIO.replace("minimal_seed", "copy")
    )
    result = runner.invoke(app, ["list-scenarios", "--config", str(config)])
    assert (result.exit_code, result.stderr) == (
        1,
        "scenario id 0x5001 is used by copy.yaml and minimal_seed.yaml\n",
    )


def test_list_scenarios_needs_a_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["list-scenarios"])
    assert (result.exit_code, result.stderr) == (
        1,
        "no semis.yaml here, and no --config\n"
        "Hint: Run from the project's directory, or pass --config semis.yaml.\n",
    )


def _no_connection(*_: object, **__: object) -> None:
    raise AssertionError("a connection was opened")


def test_seeds_writes_files_and_opens_no_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("psycopg.connect", _no_connection)
    config = _project(tmp_path, minimal_seed=SCENARIO)
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "seeds",
            str(tmp_path / "scenarios" / "minimal_seed.yaml"),
            "-o",
            str(out),
            "-c",
            str(config),
        ],
    )
    assert (result.exit_code, sorted(path.name for path in out.iterdir())) == (
        0,
        [
            "001_prep_seed.tb_continent.sql",
            "002_prep_seed.tb_country.sql",
            "minimal_seed.facts.json",
            "schema_pin.yaml",
        ],
    )


def _invoke(tmp_path: Path, command: str, *args: str, scenario: str = SCENARIO) -> Result:
    """*command* run on minimal_seed.yaml, holding *scenario*, in a fresh project."""
    config = _project(tmp_path, minimal_seed=scenario)
    path = str(tmp_path / "scenarios" / "minimal_seed.yaml")
    return runner.invoke(app, [command, path, "-c", str(config), *args])


def test_seeds_reports_each_file_and_the_pin(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = _invoke(tmp_path, "seeds", "-o", str(out))
    assert result.output == (
        "scenario minimal_seed is unpinned: its schema is not checked\n"
        "wrote 001_prep_seed.tb_continent.sql  2 rows\n"
        "wrote 002_prep_seed.tb_country.sql    4 rows\n"
        f"wrote {out / 'schema_pin.yaml'}: copy it into the scenario to pin its schema\n"
    )


def test_verbose_names_each_files_format_and_columns(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "seeds", "-o", str(tmp_path / "out"), "--verbose")
    assert result.output.splitlines()[1] == (
        "wrote 001_prep_seed.tb_continent.sql  2 rows  insert: id, identifier, name"
    )


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = _invoke(tmp_path, "seeds", "-o", str(out), "--dry-run")
    assert (result.exit_code, out.exists(), result.output) == (
        0,
        False,
        "scenario minimal_seed is unpinned: its schema is not checked\n"
        "would write 001_prep_seed.tb_continent.sql  2 rows\n"
        "would write 002_prep_seed.tb_country.sql    4 rows\n",
    )


def test_dry_run_needs_no_output(tmp_path: Path) -> None:
    assert _invoke(tmp_path, "seeds", "--dry-run").exit_code == 0


def test_writing_seeds_needs_an_output(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "seeds")
    assert (result.exit_code, "--output" in _usage(result)) == (2, True)


def test_format_chooses_the_writer(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _invoke(tmp_path, "seeds", "-o", str(out), "--format", "copy")
    assert (out / "001_prep_seed.tb_continent.sql").read_text().startswith("COPY ")


def test_no_pin_says_it_skipped_the_check(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "seeds", "-o", str(tmp_path / "out"), "--no-pin")
    assert "not checked for this run" in result.output.splitlines()[0]


def test_generate_runs_a_prep_seed_scenario_as_seeds_does(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = _invoke(tmp_path, "generate", "-o", str(out))
    assert (result.exit_code, len(list(out.glob("*.sql")))) == (0, 2)


def test_validate_checks_the_scenario_and_draws_no_rows(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "validate")
    assert (result.exit_code, result.output, list(tmp_path.glob("**/*.sql"))) == (
        0,
        "scenario minimal_seed is unpinned: its schema is not checked\n"
        "scenario minimal_seed is valid: 2 tables, 6 rows, prep-seed\n",
        [tmp_path / "schema.sql"],
    )


def test_validate_refuses_a_scenario_the_schema_cannot_run(tmp_path: Path) -> None:
    unregistered = SCENARIO.replace("tb_continent\n    count: 2", "tb_city\n    count: 2")
    result = _invoke(tmp_path, "validate", scenario=unregistered)
    assert (result.exit_code, result.stderr.splitlines()) == (
        1,
        [
            "scenario minimal_seed: table catalog.tb_city is not in the schema",
            "Hint: Name a table the schema holds, schema-qualified.",
        ],
    )


ONE_ROW = """\
scenario_id: 0x5001
name: one_row
mode: prep-seed
seed: 42
tables:
  - name: catalog.tb_continent
    count: 1
"""


def test_validate_counts_one_table_and_one_row_in_the_singular(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "validate", scenario=ONE_ROW)
    assert result.output.splitlines()[-1] == "scenario one_row is valid: 1 table, 1 row, prep-seed"


def test_seeds_counts_one_row_in_the_singular(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "seeds", "--dry-run", scenario=ONE_ROW)
    assert "would write 001_prep_seed.tb_continent.sql  1 row\n" in result.output


def _moved(tmp_path: Path) -> Path:
    """A project whose scenario is pinned, then its schema moved; its config path."""
    first = _invoke(tmp_path / "first", "seeds", "-o", str(tmp_path / "first" / "out"))
    assert first.exit_code == 0
    pinned = SCENARIO + (tmp_path / "first" / "out" / "schema_pin.yaml").read_text()
    config = _project(tmp_path / "moved", minimal_seed=pinned)
    facts = tmp_path / "first" / "out" / "minimal_seed.facts.json"
    (tmp_path / "moved" / "scenarios" / facts.name).write_text(facts.read_text())
    schema = tmp_path / "moved" / "schema.sql"
    schema.write_text(TRINITY.replace("name VARCHAR(50) NOT NULL", "name VARCHAR(50)"))
    return config


def _refused(config: Path, *args: str) -> str:
    path = str(config.parent / "scenarios" / "minimal_seed.yaml")
    result = runner.invoke(app, [*args[:1], path, "-c", str(config), *args[1:]])
    assert result.exit_code == 1
    return result.output.splitlines()[-1]


def test_a_moved_pin_under_seeds_points_at_the_pin_the_run_wrote(tmp_path: Path) -> None:
    config = _moved(tmp_path)
    hint = _refused(config, "seeds", "-o", str(tmp_path / "out"))
    assert "re-pin the scenario from the schema_pin.yaml this run wrote beside its seeds" in hint


@pytest.mark.parametrize("args", [("validate",), ("seeds", "--dry-run")])
def test_a_moved_pin_where_no_pin_is_kept_points_at_a_run_that_keeps_one(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    hint = _refused(_moved(tmp_path), *args)
    assert (
        "re-pin the scenario from the schema_pin.yaml `semis seeds -o <dir>` or `semis apply` writes "
        "beside its seeds" in hint
    )


def test_seeds_refuses_a_read_back_scenario_and_points_at_apply(tmp_path: Path) -> None:
    read_back = SCENARIO.replace("prep-seed", "read-back")
    result = _invoke(tmp_path, "seeds", "-o", str(tmp_path / "out"), scenario=read_back)
    assert (result.exit_code, result.stderr.splitlines()) == (
        1,
        [
            "scenario minimal_seed runs in read-back mode: its seeds are applied as they are "
            "written, so they need a database",
            "Hint: Run it with semis apply.",
        ],
    )
    assert not (tmp_path / "out").exists()


def test_table_writes_one_seed_file(tmp_path: Path) -> None:
    config = _project(tmp_path)
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "table",
            "catalog.tb_continent",
            "--count",
            "3",
            "--mode",
            "prep-seed",
            "--scenario-id",
            "0x5001",
            "-o",
            str(out),
            "-c",
            str(config),
        ],
    )
    assert (result.exit_code, sorted(path.name for path in out.glob("0*.sql"))) == (
        0,
        ["001_prep_seed.tb_continent.sql"],
    )
    assert (
        "'02030405-5001-8001-8000-000000000003'"
        in (out / "001_prep_seed.tb_continent.sql").read_text()
    )


def test_table_needs_a_mode_and_a_scenario_id(tmp_path: Path) -> None:
    config = _project(tmp_path)
    table = ["table", "catalog.tb_continent", "--count", "1", "-c", str(config), "--dry-run"]
    missing_id = runner.invoke(app, [*table, "--mode", "prep-seed"])
    missing_mode = runner.invoke(app, [*table, "--scenario-id", "0x5001"])
    assert (missing_id.exit_code, missing_mode.exit_code) == (2, 2)
    assert ("--scenario-id" in _usage(missing_id), "--mode" in _usage(missing_mode)) == (
        True,
        True,
    )


def test_table_dry_run_reports_its_one_file(tmp_path: Path) -> None:
    config = _project(tmp_path)
    result = runner.invoke(
        app,
        [
            "table",
            "catalog.tb_continent",
            "--count=2",
            "--mode=prep-seed",
            "--scenario-id=0x5001",
            "--seed=7",
            "--dry-run",
            "--verbose",
            "--format=copy",
            "-c",
            str(config),
        ],
    )
    assert result.output.splitlines()[-1] == (
        "would write 001_prep_seed.tb_continent.sql  2 rows  copy: id, identifier, name"
    )


def test_seed_locale_and_scenario_id_override_the_scenario_and_say_so(tmp_path: Path) -> None:
    out = tmp_path / "out"
    options = ["--seed", "7", "--locale", "fr_FR", "--scenario-id", "0x5002"]
    result = _invoke(tmp_path, "seeds", "-o", str(out), *options)
    assert result.output.splitlines()[:3] == [
        "scenario minimal_seed runs with scenario_id 0x5002, not 0x5001, for this run",
        "scenario minimal_seed runs with seed 7, not 42, for this run",
        "scenario minimal_seed runs with locale fr_FR, not en_US, for this run",
    ]
    assert "'02030405-5002-8001-" in (out / "001_prep_seed.tb_continent.sql").read_text()


def test_init_scenario_writes_a_template_with_the_next_free_id(tmp_path: Path) -> None:
    config = _project(tmp_path, minimal_seed=SCENARIO)
    result = runner.invoke(app, ["init-scenario", "demo", "--mode", "read-back", "-c", str(config)])
    written = tmp_path / "scenarios" / "demo.yaml"
    assert result.output == f"wrote {written}: scenario demo, scenario_id 0x5002, read-back\n"
    assert _invoke(tmp_path / "again", "validate", scenario=written.read_text()).exit_code == 0


def test_init_scenario_in_an_empty_project_starts_at_0x5001(tmp_path: Path) -> None:
    config = _project(tmp_path)
    runner.invoke(app, ["init-scenario", "first", "--mode", "prep-seed", "-c", str(config)])
    assert "scenario_id: 0x5001\n" in (tmp_path / "scenarios" / "first.yaml").read_text()


def test_init_scenario_refuses_to_overwrite(tmp_path: Path) -> None:
    config = _project(tmp_path, minimal_seed=SCENARIO)
    result = runner.invoke(
        app, ["init-scenario", "minimal_seed", "--mode", "prep-seed", "-c", str(config)]
    )
    assert (result.exit_code, "already exists" in result.stderr) == (1, True)
    assert (tmp_path / "scenarios" / "minimal_seed.yaml").read_text() == SCENARIO


def test_init_scenario_refuses_a_name_that_is_no_file_name(tmp_path: Path) -> None:
    config = _project(tmp_path)
    result = runner.invoke(app, ["init-scenario", "../x", "--mode", "prep-seed", "-c", str(config)])
    assert (result.exit_code, "is not a scenario name" in result.stderr) == (1, True)


def test_row_contract_failure_exits_1_and_names_the_column(tmp_path: Path) -> None:
    too_long = SCENARIO.replace(
        "    count: 2\n", f"    count: 2\n    overrides:\n      name: {'x' * 60}\n"
    )
    result = _invoke(tmp_path, "seeds", "-o", str(tmp_path / "out"), scenario=too_long)
    assert (result.exit_code, "catalog.tb_continent.name" in result.stderr) == (1, True)


def test_a_confiture_refusal_keeps_its_exit_code_and_hint(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "apply", "-o", str(tmp_path / "out"), "-d", "mysql://x")
    assert (result.exit_code, result.stderr.splitlines()) == (
        5,
        [
            "Invalid --database-url: must start with postgresql:// or postgres://, got: mysql://x",
            "Hint: Use format: postgresql://user:password@host:port/database",
        ],
    )


def test_a_refusal_never_shows_a_urls_password(tmp_path: Path) -> None:
    url = "mysql://reader:s3cret@db.example/app"
    result = _invoke(tmp_path, "apply", "-o", str(tmp_path / "out"), "-d", url)
    assert "s3cret" not in result.stderr
    assert "mysql://reader:***@db.example/app" in result.stderr


def test_a_database_that_cannot_be_reached_is_a_refusal(tmp_path: Path) -> None:
    url = "postgresql://reader:s3cret@127.0.0.1:1/app"
    result = _invoke(tmp_path, "apply", "-o", str(tmp_path / "out"), "-d", url)
    assert (result.exit_code, result.exception.__class__) == (1, SystemExit)
    first, *_, hint = result.stderr.splitlines()
    assert first.startswith("cannot connect to PostgreSQL at 127.0.0.1:1/app: ")
    assert hint.startswith("Hint: Check that PostgreSQL is running there")
    assert "s3cret" not in result.stderr


def test_a_url_libpq_cannot_read_is_a_refusal_that_never_shows_its_password(
    tmp_path: Path,
) -> None:
    url = "postgresql://reader:s3cr%zzet@127.0.0.1:1/app"
    result = _invoke(tmp_path, "apply", "-o", str(tmp_path / "out"), "-d", url)
    assert (result.exit_code, result.exception.__class__) == (1, SystemExit)
    assert result.stderr.splitlines() == [
        "cannot connect to PostgreSQL: the database URL is malformed",
        "Hint: Check the URL's form, postgresql://user:password@host:port/database, and "
        "percent-encode any reserved character in its password.",
    ]


def test_every_command_passes_through_the_error_boundary() -> None:
    unguarded = [
        info.name or getattr(info.callback, "__name__", "?")
        for info in app.registered_commands
        if not hasattr(info.callback, "__wrapped__")
    ]
    assert unguarded == []


def test_a_malformed_uuid_is_a_usage_error() -> None:
    result = runner.invoke(app, ["decode-uuid", "not-a-uuid"])
    assert (result.exit_code, "not a valid uuid" in result.output.lower()) == (2, True)


def _group() -> TyperGroup:
    group = typer.main.get_command(app)
    assert isinstance(group, TyperGroup)
    return group


@pytest.mark.parametrize("command", sorted(_group().commands))
def test_every_command_has_help(command: str) -> None:
    assert (_group().commands[command].help or "").strip()


def test_main_is_the_installed_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv", ["semis", "decode-uuid", "02030405-5001-8001-8000-000000000042"]
    )
    with pytest.raises(SystemExit) as exit_:
        main()
    assert exit_.value.code == 0


def test_a_scenario_names_a_provider_the_project_enables(tmp_path: Path) -> None:
    named = SCENARIO + "    providers: {name: i18n.country_name}\n"
    config = _project(tmp_path, minimal_seed=named)
    scenario = str(tmp_path / "scenarios" / "minimal_seed.yaml")
    refused = runner.invoke(app, ["validate", scenario, "--config", str(config)])
    config.write_text(SEMIS_YAML + "providers: [i18n]\n")
    enabled = runner.invoke(app, ["validate", scenario, "--config", str(config)])
    assert "names provider 'i18n.country_name', which is not registered" in refused.output
    assert (refused.exit_code, enabled.exit_code) == (1, 0)


BAD_UUID_SEED = """\
INSERT INTO prep_seed.tb_continent (id, identifier, name) VALUES
('00000000-0000-0000-0000-000000000001', 'a', 'A'),
('not-a-uuid', 'b', 'B');
"""


def _seeds_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project reading its schema from a directory, with no database URL anywhere."""
    for name in ("CONFITURE_DATABASE_URL", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "schema").mkdir()
    (tmp_path / "schema" / "010_schema.sql").write_text(TRINITY)
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "minimal_seed.yaml").write_text(SCENARIO)
    config = tmp_path / "semis.yaml"
    config.write_text(SEMIS_YAML.replace("ddl: schema.sql", "ddl: schema"))
    return config


def test_validate_seeds_reports_by_severity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _seeds_project(tmp_path, monkeypatch)
    (tmp_path / "seeds").mkdir()
    (tmp_path / "seeds" / "001_continent.sql").write_text(BAD_UUID_SEED)
    result = runner.invoke(
        app, ["validate-seeds", "--seeds", str(tmp_path / "seeds"), "-c", str(config)]
    )
    assert (result.exit_code, result.stdout.splitlines()) == (
        1,
        [
            "no database URL: levels 1-3 only; levels 4-5 load the seeds and run the "
            "resolvers against a database",
            "validated 1 seed file at levels 1-3",
            "ERROR INVALID_UUID_FORMAT 001_continent.sql:3",
            "  Invalid UUID 'not-a-uuid' in prep_seed.tb_continent.id, row 2 (a column typed "
            "uuid in the schema): expected 32 hex digits, 8-4-4-4-12",
            "  hint: Use valid UUID format (see RFC 4122)",
            "WARNING MISSING_RESOLVER_FUNCTION schema",
            f"  no resolution function found in {tmp_path / 'schema'}: no routine it defines "
            "is named fn_resolve*, so levels 3-5 check no resolver",
            "2 findings: 1 ERROR, 1 WARNING",
        ],
    )


def _validate_seeds(tmp_path: Path, config: Path, *args: str) -> Result:
    scenario = str(tmp_path / "scenarios" / "minimal_seed.yaml")
    return runner.invoke(app, ["validate-seeds", scenario, "-c", str(config), *args])


def test_validate_seeds_rehearses_a_scenario_and_opens_no_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _seeds_project(tmp_path, monkeypatch)
    monkeypatch.setattr("psycopg.connect", _no_connection)
    result = _validate_seeds(tmp_path, config)
    lines = result.stdout.splitlines()
    assert result.exit_code == 0
    assert lines[0].startswith("no database URL: levels 1-3 only")
    assert "validated 2 seed files at levels 1-3" in lines
    assert lines[-1] == "1 finding: 1 WARNING"


def test_validate_seeds_takes_a_scenario_or_a_seeds_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _seeds_project(tmp_path, monkeypatch)
    (tmp_path / "seeds").mkdir()
    both = _validate_seeds(tmp_path, config, "--seeds", str(tmp_path / "seeds"))
    neither = runner.invoke(app, ["validate-seeds", "-c", str(config)])
    assert (both.exit_code, neither.exit_code) == (2, 2)
    assert "give a scenario, or --seeds DIR" in _usage(both)


def test_levels_4_and_5_need_a_database_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _seeds_project(tmp_path, monkeypatch)
    result = _validate_seeds(tmp_path, config, "--max-level", "4")
    assert (result.exit_code, result.stderr.splitlines()[0]) == (
        1,
        "no database URL: this command connects to a database",
    )


def test_an_ambient_database_url_alone_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _seeds_project(tmp_path, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql:///ambient")
    result = _validate_seeds(tmp_path, config)
    assert result.exit_code == 5
    assert "refusing to run against an ambient DATABASE_URL" in result.stderr


def test_a_max_level_of_3_or_below_reaches_no_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _seeds_project(tmp_path, monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql:///ambient")
    monkeypatch.setattr("psycopg.connect", _no_connection)
    result = _validate_seeds(tmp_path, config, "--max-level", "2")
    assert (result.exit_code, result.stdout.splitlines()) == (
        0,
        [
            "scenario minimal_seed is unpinned: its schema is not checked",
            "validated 2 seed files at levels 1-2",
            "no findings",
        ],
    )


def test_validate_seeds_refuses_a_read_back_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _seeds_project(tmp_path, monkeypatch)
    scenario = tmp_path / "scenarios" / "minimal_seed.yaml"
    scenario.write_text(SCENARIO.replace("prep-seed", "read-back"))
    result = _validate_seeds(tmp_path, config, "--max-level", "3")
    assert result.exit_code == 1
    assert "confiture's five levels judge prep-seed seeds" in result.stderr


def test_decode_uuid_decodes_beside_a_scenarios_directory_that_does_not_read(
    tmp_path: Path,
) -> None:
    config = _project(tmp_path, minimal_seed=SCENARIO, b=SCENARIO.replace("0x5001", '"0x7009"'))
    uuid = "02030405-5001-8001-8000-000000000042"
    result = runner.invoke(app, ["decode-uuid", uuid, "--config", str(config)])
    assert (result.exit_code, result.stdout.splitlines()[1]) == (0, "scenario_id  0x5001")
    assert result.stderr.splitlines()[0] == (
        f"the scenarios directory does not read: {tmp_path / 'scenarios' / 'b.yaml'}: "
        "scenario_id is '0x7009', a string"
    )


def test_table_refuses_a_bare_name(tmp_path: Path) -> None:
    config = _project(tmp_path)
    result = runner.invoke(
        app,
        [
            *("table", "tb_continent", "--count", "1", "--mode", "prep-seed"),
            *("--scenario-id", "0x5001", "-c", str(config), "--dry-run"),
        ],
    )
    assert (result.exit_code, result.stderr.splitlines()) == (
        1,
        [
            "table tb_continent is not schema-qualified",
            "Hint: Name it with its schema, e.g. catalog.tb_city.",
        ],
    )


MALFORMED = [
    "cannot connect to PostgreSQL: the database URL is malformed",
    "Hint: Check the URL's form, postgresql://user:password@host:port/database, and "
    "percent-encode any reserved character in its password.",
]


def _live_project(tmp_path: Path) -> Path:
    """A project whose schema is read from a database."""
    config = _project(tmp_path, minimal_seed=SCENARIO)
    config.write_text(
        SEMIS_YAML.replace("  ddl: schema.sql\n", "  database:\n    schemas: [catalog]\n")
    )
    return config


def test_a_database_schema_refuses_a_malformed_url_before_confiture_reads_it(
    tmp_path: Path,
) -> None:
    config = _live_project(tmp_path)
    scenario = str(tmp_path / "scenarios" / "minimal_seed.yaml")
    url = "postgresql://u:s3cr%t@localhost/x"
    result = runner.invoke(app, ["validate", scenario, "-c", str(config), "-d", url])
    assert (result.exit_code, result.stderr.splitlines()) == (1, MALFORMED)


PASSWORDS = [
    ("postgresql://u:p@ss@127.0.0.1:1/db", "p@ss"),
    ("postgresql://u:pa:ss@127.0.0.1:1/db", "pa:ss"),
    ("postgresql://u:pa/ss@127.0.0.1:1/db", "pa/ss"),
    ("host=127.0.0.1 port=1 password=s3cr3t dbname=x", "s3cr3t"),
    ("password='s 3cr' host=127.0.0.1 port=1", "s 3cr"),
    ("postgresql://127.0.0.1:1/db?password=s3cr3t", "s3cr3t"),
]


def _fragments(password: str) -> set[str]:
    """Every run of the password's characters longer than 2."""
    return {password[i : i + 3] for i in range(len(password) - 2)}


@pytest.mark.parametrize(("url", "password"), PASSWORDS)
@pytest.mark.parametrize("command", ["validate", "apply", "validate-seeds"])
def test_no_command_prints_any_part_of_a_password(
    tmp_path: Path, url: str, password: str, command: str
) -> None:
    if command == "validate":
        config = _live_project(tmp_path)
    else:
        config = _project(tmp_path, minimal_seed=SCENARIO)
        config.write_text(SEMIS_YAML + "prep_seed:\n  schema_dir: .\n")
    scenario = str(tmp_path / "scenarios" / "minimal_seed.yaml")
    args = ["-o", str(tmp_path / "out")] if command == "apply" else []
    result = runner.invoke(app, [command, scenario, "-c", str(config), "-d", url, *args])
    assert result.exit_code != 0
    assert [part for part in _fragments(password) if part in result.stderr] == []


@pytest.mark.parametrize(("url", "password"), PASSWORDS)
def test_the_boundary_masks_every_form_a_password_takes(url: str, password: str) -> None:
    masked = _masked(f"Invalid --database-url: got: {url}\nHint: see {url}")
    assert "***" in masked
    assert [part for part in _fragments(password) if part in masked] == []


def test_the_boundary_keeps_the_placeholder_a_format_hint_spells_out() -> None:
    hint = "Hint: Use format: postgresql://user:password@host:port/database"
    assert _masked(hint) == hint


@pytest.mark.parametrize(
    "url", ["postgresql://u:p@ss@127.0.0.1:1/db", "postgresql://u:pa/ss@127.0.0.1:1/db"]
)
def test_a_url_libpq_reads_as_another_host_or_port_is_malformed(url: str) -> None:
    """libpq reads the first as host ss@127.0.0.1, the second as host u, port pa: a
    password with a reserved character in it, not percent-encoded."""
    with pytest.raises(UnreachableDatabaseError) as refused:
        checked_url(url)
    assert str(refused.value).splitlines() == MALFORMED


def test_a_finding_outside_the_seeds_and_the_schema_is_named_by_its_path(
    tmp_path: Path,
) -> None:
    elsewhere = tmp_path / "elsewhere.sql"
    assert _where(elsewhere, 3, tmp_path / "seeds", tmp_path / "schema") == f"{elsewhere}:3"


DRY_RUN = {
    "seeds": "Generate and check every row, then write nothing.",
    "apply": "Apply every row, then roll back: nothing is kept.",
    "generate": "Read-back applies every row and rolls back; prep-seed writes no file: "
    "nothing is kept.",
    "table": "Read-back applies every row and rolls back; prep-seed writes no file: "
    "nothing is kept.",
}


@pytest.mark.parametrize(("command", "said"), DRY_RUN.items())
def test_dry_run_says_what_it_does_for_each_command(command: str, said: str) -> None:
    result = runner.invoke(app, [command, "--help"], env={"COLUMNS": "300"})
    shown = " ".join(_usage(result).replace("│", " ").split())
    assert f"--dry-run {said}" in shown
