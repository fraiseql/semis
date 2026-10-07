"""The ``semis`` command's entry point and its subcommands.

Each command loads what it needs and hands it to the library; none of them decides what a
row holds.
"""

import re
from collections import Counter
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

import typer

from fraiseql_semis.cli.options import (
    Config,
    Count,
    DatabaseUrl,
    DryRunApply,
    DryRunInMode,
    DryRunWrite,
    Format,
    Locale,
    MaxLevel,
    Mode,
    Name,
    NoPin,
    Output,
    ScenarioFile,
    ScenarioId,
    ScenarioToValidate,
    Seed,
    SeedsDir,
    SemanticUUID,
    Table,
    Verbose,
)
from fraiseql_semis.errors import ProjectError, ScenarioError, SemisError
from fraiseql_semis.project import Project
from fraiseql_semis.readback import exclusive, transaction
from fraiseql_semis.scenario import (
    Run,
    Scenario,
    ScenarioEntry,
    ScenarioManager,
    catalogue,
    shared_ids,
    single_table,
)
from fraiseql_semis.scenario import init_scenario as new_scenario
from fraiseql_semis.schema import ConfiturError
from fraiseql_semis.seeds import PrepSeedReport
from fraiseql_semis.uuid_generator import SemanticUUIDGenerator

app = typer.Typer(no_args_is_help=True)


def _refusals[**P, R](command: Callable[P, R]) -> Callable[P, R]:
    """The one error boundary: a refusal is printed to stderr and becomes the exit code.

    semis' own refusals exit with their ``exit_code`` (1); confiture's propagate unwrapped
    (D11) and keep theirs, with their hint. Anything else is a bug, and keeps its
    traceback.
    """

    @wraps(command)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return command(*args, **kwargs)
        except (SemisError, ConfiturError) as refusal:
            typer.echo(_masked(str(refusal)), err=True)
            raise typer.Exit(refusal.exit_code) from refusal

    return run


# A URL's password, up to the last @ of the URL, unless it is the placeholder a format
# hint spells out.
_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]*:)(?!password@)\S*@")
# A keyword DSN's password, quoted or bare, or a URL query string's.
_KEYWORD = re.compile(r"(\bpassword\s*=\s*)(?:'(?:[^'\\]|\\.)*'|[^\s&]+)")


def _masked(text: str) -> str:
    """*text*, every password in it replaced: a refusal may repeat the URL or the
    keyword DSN it was given."""
    return _KEYWORD.sub(r"\1***", _USERINFO.sub(r"\1***@", text))


@app.callback()
def semis() -> None:
    """Reproducible seed data for PostgreSQL trinity-pattern schemas."""


@app.command("decode-uuid")
@_refusals
def decode_uuid(value: SemanticUUID, config: Config = None) -> None:
    """Print the table code, scenario id, version and sequence a UUID carries.

    With a project, the table and the scenario they name are printed beside them; a
    scenarios directory that does not read is said on stderr, and names no scenario.
    """
    try:
        fields = SemanticUUIDGenerator.decode(value)
    except ValueError as refused:
        raise typer.BadParameter(str(refused), param_hint="'VALUE'") from refused
    project = Project.find(config)
    table = scenario = ""
    if project is not None:
        table = project.table_codes.table_for(fields.table_code) or "no table has this code"
        try:
            scenario = _scenario_names(catalogue(project.scenarios), fields.scenario_id)
        except ScenarioError as refused:
            typer.echo(f"the scenarios directory does not read: {refused.message}", err=True)
    _field("table_code", f"{fields.table_code:#010x}", table)
    _field("scenario_id", f"{fields.scenario_id:#06x}", scenario)
    _field("version", str(fields.version))
    _field("sequence", str(fields.sequence))


@app.command("list-scenarios")
@_refusals
def list_scenarios(config: Config = None) -> None:
    """List the project's scenarios: id, mode, name and file."""
    project = _require(config)
    entries = catalogue(project.scenarios)
    width = max((len(entry.name) for entry in entries), default=0)
    for entry in entries:
        path = entry.path.relative_to(project.scenarios)
        typer.echo(f"{entry.id:#06x}  {entry.mode:<9}  {entry.name:<{width}}  {path}")
    shared = shared_ids(entries)
    for id_, found in shared.items():
        files = " and ".join(str(entry.path.relative_to(project.scenarios)) for entry in found)
        typer.echo(f"scenario id {id_:#06x} is used by {files}", err=True)
    if shared:
        raise typer.Exit(1)


@app.command()
@_refusals
def seeds(  # noqa: PLR0913 — one argument; the rest are the run's options
    scenario: ScenarioFile,
    *,
    output: Output = None,
    config: Config = None,
    database_url: DatabaseUrl = None,
    format: Format = None,
    dry_run: DryRunWrite = False,
    no_pin: NoPin = False,
    verbose: Verbose = False,
    scenario_id: ScenarioId = None,
    seed: Seed = None,
    locale: Locale = None,
) -> None:
    """Write a prep-seed scenario's seed files. No database is reached."""
    options = _Options(output, database_url, format, dry_run, no_pin, verbose)
    _, manager, loaded = _load(scenario, config, options, scenario_id, seed, locale)
    if loaded.mode == "read-back":
        raise ScenarioError(
            f"scenario {loaded.name} runs in read-back mode: its seeds are applied as they "
            "are written, so they need a database",
            resolution_hint="Run it with semis apply.",
        )
    _write(manager, loaded, options)


@app.command()
@_refusals
def generate(  # noqa: PLR0913 — one argument; the rest are the run's options
    scenario: ScenarioFile,
    *,
    output: Output = None,
    config: Config = None,
    database_url: DatabaseUrl = None,
    format: Format = None,
    dry_run: DryRunInMode = False,
    no_pin: NoPin = False,
    verbose: Verbose = False,
    scenario_id: ScenarioId = None,
    seed: Seed = None,
    locale: Locale = None,
) -> None:
    """Run a scenario in its declared mode: prep-seed writes its seeds, read-back applies them."""
    options = _Options(output, database_url, format, dry_run, no_pin, verbose)
    project, manager, loaded = _load(scenario, config, options, scenario_id, seed, locale)
    _in_its_mode(project, manager, loaded, options)


@app.command()
@_refusals
def apply(  # noqa: PLR0913 — one argument; the rest are the run's options
    scenario: ScenarioFile,
    *,
    output: Output = None,
    config: Config = None,
    database_url: DatabaseUrl = None,
    format: Format = None,
    dry_run: DryRunApply = False,
    no_pin: NoPin = False,
    verbose: Verbose = False,
    scenario_id: ScenarioId = None,
    seed: Seed = None,
    locale: Locale = None,
) -> None:
    """Write a scenario's seeds and apply them, in one transaction committed at the end."""
    options = _Options(output, database_url, format, dry_run, no_pin, verbose)
    project, manager, loaded = _load(scenario, config, options, scenario_id, seed, locale)
    _apply(project, manager, loaded, options)


@app.command()
@_refusals
def table(  # noqa: PLR0913 — one argument; the rest are the run's options
    name: Table,
    *,
    count: Count,
    mode: Mode,
    scenario_id: ScenarioId,
    output: Output = None,
    config: Config = None,
    database_url: DatabaseUrl = None,
    format: Format = None,
    dry_run: DryRunInMode = False,
    verbose: Verbose = False,
    seed: Seed = None,
) -> None:
    """Generate one table's rows, as a scenario of that table alone, in the mode given."""
    assert scenario_id is not None  # Typer requires it: the signature gives no default
    options = _Options(output, database_url, format, dry_run, False, verbose)
    project = _require(config)
    manager = project.manager(database_url=database_url)
    scenario = single_table(name, count, mode=mode, scenario_id=scenario_id, seed=seed)
    _in_its_mode(project, manager, scenario, options)


@app.command("init-scenario")
@_refusals
def init_scenario(name: Name, *, mode: Mode, config: Config = None) -> None:
    """Write a new scenario listing the project's tables, with the next free scenario id."""
    project = _require(config)
    path, scenario_id = new_scenario(
        project.scenarios, name, mode=mode, tables=project.table_codes.tables()
    )
    typer.echo(f"wrote {path}: scenario {name}, scenario_id {scenario_id:#06x}, {mode}")


@app.command()
@_refusals
def validate(
    scenario: ScenarioFile,
    config: Config = None,
    database_url: DatabaseUrl = None,
    no_pin: NoPin = False,
) -> None:
    """Check a scenario against the schema and its pin, drawing no rows."""
    options = _Options(None, database_url, None, False, no_pin, False)
    _, manager, loaded = _load(scenario, config, options)
    _notices(manager.check(loaded, no_pin=no_pin))
    tables, rows = len(loaded.tables), sum(spec.count for spec in loaded.tables)
    typer.echo(
        f"scenario {loaded.name} is valid: {tables} table{_plural(tables)}, "
        f"{rows} row{_plural(rows)}, {loaded.mode}"
    )


@app.command("validate-seeds")
@_refusals
def validate_seeds(  # noqa: PLR0913 — one argument; the rest are the validation's options
    scenario: ScenarioToValidate = None,
    *,
    seeds: SeedsDir = None,
    max_level: MaxLevel = None,
    config: Config = None,
    database_url: DatabaseUrl = None,
    no_pin: NoPin = False,
) -> None:
    """Judge prep-seed seeds by confiture's five levels, a scenario's or a directory's.

    Levels 4-5 load the seeds and run the resolvers on the database, rolled back.
    """
    if (scenario is None) == (seeds is None):
        raise typer.BadParameter("give a scenario, or --seeds DIR", param_hint="SCENARIO")
    project = _require(config)
    url = _validation_url(project, database_url, max_level)
    level = max_level or (_LEVELS if url else _STATIC_LEVELS)
    manager = project.manager(database_url=database_url)
    schema_dir = project.resolvers()
    with nullcontext() if url is None else transaction(url, commit=False) as connection:
        if scenario is None:
            assert seeds is not None  # exactly one of the two, checked above
            report = manager.validate_seeds(
                seeds,
                schema_dir=schema_dir,
                max_level=level,
                connection=connection,
                catalog_schema=project.catalog_schema,
            )
        else:
            validation = manager.validate(
                manager.load(scenario),
                schema_dir=schema_dir,
                max_level=level,
                connection=connection,
                catalog_schema=project.catalog_schema,
                no_pin=no_pin,
            )
            _notices(validation.run.notices)
            report, seeds = validation.report, validation.run.pin_path.parent
    files = len(report.scanned_files)
    typer.echo(f"validated {files} seed file{_plural(files)} at levels 1-{level}")
    if _findings(report, seeds, schema_dir):
        raise typer.Exit(1)


def main() -> None:
    app()


@dataclass(frozen=True)
class _Options:
    """How one run is carried out, as its command line says."""

    output: Path | None
    database_url: str | None
    format: Format
    dry_run: bool
    no_pin: bool
    verbose: bool

    def out_dir(self) -> Path:
        if self.output is None:
            raise typer.BadParameter("seeds are written to --output DIR", param_hint="--output")
        return self.output


def _load(  # noqa: PLR0913, PLR0917 — the scenario, where it is read from, one run's overrides
    scenario: Path,
    config: Path | None,
    options: _Options,
    scenario_id: int | None = None,
    seed: int | None = None,
    locale: str | None = None,
) -> tuple[Project, ScenarioManager, Scenario]:
    """The project, its scenario manager, and the scenario at *scenario* loaded by it
    with this run's overrides, each said."""
    project = _require(config)
    manager = project.manager(database_url=options.database_url)
    loaded, notices = manager.load(scenario).for_run(
        scenario_id=scenario_id, seed=seed, locale=locale
    )
    _notices(notices)
    return project, manager, loaded


def _in_its_mode(
    project: Project, manager: ScenarioManager, scenario: Scenario, options: _Options
) -> None:
    """Prep-seed writes its seeds; read-back needs a database, and applies them."""
    if scenario.mode == "read-back":
        _apply(project, manager, scenario, options)
    else:
        _write(manager, scenario, options)


def _write(manager: ScenarioManager, scenario: Scenario, options: _Options) -> None:
    """*scenario*'s seeds written, or rehearsed and reported under ``--dry-run``."""
    format, no_pin = options.format, options.no_pin
    if options.dry_run:
        run = manager.rehearse(scenario, format=format, no_pin=no_pin)
        _report(run, "would write", options.verbose)
        return
    run = manager.execute(scenario, options.out_dir(), format=format, no_pin=no_pin)
    _report(run, "wrote", options.verbose)
    _pin_written(run)


def _apply(
    project: Project, manager: ScenarioManager, scenario: Scenario, options: _Options
) -> None:
    """*scenario* applied in one transaction: committed, or under ``--dry-run`` rolled back.

    The scenario's lock is held around that transaction, so a second apply of it waits.
    """
    url = project.database_url(options.database_url, mutating=True)
    format, no_pin = options.format, options.no_pin
    out_dir = None if options.dry_run else options.out_dir()
    with (
        exclusive(url, scenario.id),
        transaction(url, commit=not options.dry_run) as connection,
    ):
        if out_dir is None:
            run = manager.rehearse(scenario, connection=connection, format=format, no_pin=no_pin)
        else:
            run = manager.apply(
                scenario, out_dir, connection=connection, format=format, no_pin=no_pin
            )
    if out_dir is None:
        _report(run, "would apply", options.verbose)
        typer.echo("rolled back: nothing was written or applied")
        return
    _report(run, "applied", options.verbose)
    _pin_written(run)
    typer.echo("committed")


def _report(run: Run, verb: str, verbose: bool) -> None:
    """The pin check's notices, then one line per seed file."""
    _notices(run.notices)
    width = max((len(seed.path.name) for seed in run.seeds), default=0)
    for seed in run.seeds:
        line = f"{verb} {seed.path.name:<{width}}  {seed.rows} row{_plural(seed.rows)}"
        if verbose:
            line += f"  {seed.format}: {', '.join(seed.columns)}"
        typer.echo(line)


def _pin_written(run: Run) -> None:
    typer.echo(f"wrote {run.pin_path}: copy it into the scenario to pin its schema")


def _notices(notices: tuple[str, ...]) -> None:
    for notice in notices:
        typer.echo(notice)


def _require(config: Config) -> Project:
    project = Project.find(config)
    if project is None:
        raise ProjectError(
            "no semis.yaml here, and no --config",
            resolution_hint="Run from the project's directory, or pass --config semis.yaml.",
        )
    return project


def _scenario_names(entries: tuple[ScenarioEntry, ...], scenario_id: int) -> str:
    names = sorted(entry.name for entry in entries if entry.id == scenario_id)
    if not names:
        return "no scenario file has this id"
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names)} ({len(names)} scenario files share this id)"


def _field(label: str, value: str, name: str = "") -> None:
    typer.echo(f"{label:<13}{value:<13}{name}".rstrip())


_STATIC_LEVELS = 3  # levels 1-3 read files; 4-5 load the seeds into a database
_LEVELS = 5
_SEVERITIES = ("CRITICAL", "ERROR", "WARNING", "INFO")
_FAILING = {"CRITICAL", "ERROR"}


def _validation_url(project: Project, flag: str | None, max_level: int | None) -> str | None:
    """The database levels 4-5 run on, as a command that writes finds it.

    Asked for by ``--max-level``, it must be found; by default, none found means levels
    1-3 alone, and says so.
    """
    if max_level is not None:
        if max_level <= _STATIC_LEVELS:
            return None
        return project.database_url(flag, mutating=True)
    url = project.find_database_url(flag, mutating=True)
    if url is None:
        typer.echo(
            "no database URL: levels 1-3 only; levels 4-5 load the seeds and run the "
            "resolvers against a database"
        )
    return url


def _findings(report: PrepSeedReport, seeds_dir: Path, schema_dir: Path) -> bool:
    """*report*'s findings, most severe first, then their count; whether one fails the run."""
    violations = sorted(
        report.violations, key=lambda violation: _SEVERITIES.index(violation.severity.value)
    )
    counts = Counter(violation.severity.value for violation in violations)
    for violation in violations:
        where = _where(Path(violation.file_path), violation.line_number, seeds_dir, schema_dir)
        typer.echo(f"{violation.severity.value} {violation.pattern.value} {where}")
        typer.echo(f"  {violation.message}")
        if violation.suggestion:
            typer.echo(f"  hint: {violation.suggestion}")
    if not violations:
        typer.echo("no findings")
        return False
    tally = ", ".join(f"{counts[name]} {name}" for name in _SEVERITIES if name in counts)
    typer.echo(f"{len(violations)} finding{_plural(len(violations))}: {tally}")
    return bool(_FAILING & counts.keys())


def _where(path: Path, line: int | None, seeds_dir: Path, schema_dir: Path) -> str:
    """A seed by its path among the seeds, a schema file by its path in the schema tree,
    then its line; a directory is named alone."""
    shown = str(path)
    if path.is_relative_to(seeds_dir):
        shown = str(path.relative_to(seeds_dir))
    elif path.is_relative_to(schema_dir):
        shown = str(Path(schema_dir.name) / path.relative_to(schema_dir))
    if path.is_dir() or not line:
        return shown
    return f"{shown}:{line}"


def _plural(count: int) -> str:
    return "" if count == 1 else "s"
