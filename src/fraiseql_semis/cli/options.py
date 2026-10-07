"""The arguments and options the ``semis`` commands take, each declared once.

A flag two commands take is spelled here and nowhere else, as confiture's CLI does, so
two declarations cannot drift apart; ``tests/unit/test_cli_options.py`` fails on a
``typer.Option`` or ``typer.Argument`` declared anywhere else.
"""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from fraiseql_semis.seeds import Format as SeedFormat
from fraiseql_semis.seeds import Mode as SeedMode

SemanticUUID = Annotated[UUID, typer.Argument(help="A UUID semis encoded.", show_default=False)]
Config = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        help="The project file: schema, table codes and scenarios (default: ./semis.yaml).",
        show_default=False,
    ),
]
ScenarioFile = Annotated[
    Path,
    typer.Argument(help="The scenario's YAML file.", exists=True, dir_okay=False),
]
Output = Annotated[
    Path | None,
    typer.Option("--output", "-o", help="The directory the seed files are written to."),
]
Format = Annotated[
    SeedFormat | None,
    typer.Option(help="The writer: insert or copy (default: the mode's own).", show_default=False),
]
DryRunWrite = Annotated[
    bool,
    typer.Option("--dry-run", help="Generate and check every row, then write nothing."),
]
DryRunApply = Annotated[
    bool,
    typer.Option("--dry-run", help="Apply every row, then roll back: nothing is kept."),
]
DryRunInMode = Annotated[
    bool,
    typer.Option(
        "--dry-run",
        help="Read-back applies every row and rolls back; prep-seed writes no file: nothing "
        "is kept.",
    ),
]
NoPin = Annotated[
    bool,
    typer.Option("--no-pin", help="Skip the schema pin check for this run, and say so."),
]
Verbose = Annotated[
    bool,
    typer.Option("--verbose", "-v", help="Name each seed file's format and columns."),
]
Table = Annotated[str, typer.Argument(help="The schema-qualified table, e.g. catalog.tb_city.")]
Count = Annotated[int, typer.Option(help="How many rows.", min=0)]
Mode = Annotated[
    SeedMode,
    typer.Option(help="How a child learns its parent's key: prep-seed or read-back."),
]
ScenarioId = Annotated[
    int | None,
    typer.Option(
        help="The scenario id the UUIDs carry, in hex (0x5001).",
        parser=lambda text: int(text, 0),
        metavar="<hex>",
        show_default=False,
    ),
]
Seed = Annotated[int | None, typer.Option(help="The Faker seed, for this run.", show_default=False)]
Locale = Annotated[
    str | None, typer.Option(help="The Faker locale, for this run.", show_default=False)
]
Name = Annotated[str, typer.Argument(help="The new scenario's name, and its file's.")]
DatabaseUrl = Annotated[
    str | None,
    typer.Option(
        "--database-url",
        "-d",
        help=(
            "The PostgreSQL URL. Wins over CONFITURE_DATABASE_URL, the project's env: and "
            "DATABASE_URL, which a command that writes does not take alone."
        ),
        show_default=False,
    ),
]
ScenarioToValidate = Annotated[
    Path | None,
    typer.Argument(
        help="The prep-seed scenario to rehearse and validate (or --seeds DIR).",
        exists=True,
        dir_okay=False,
        show_default=False,
    ),
]
SeedsDir = Annotated[
    Path | None,
    typer.Option(
        "--seeds",
        help="Validate the seed files in this directory instead of a scenario's.",
        exists=True,
        file_okay=False,
        show_default=False,
    ),
]
MaxLevel = Annotated[
    int | None,
    typer.Option(
        "--max-level",
        help="The last level run, 1 to 5 (default: 5 with a database URL, else 3).",
        min=1,
        max=5,
        show_default=False,
    ),
]
