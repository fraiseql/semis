"""A scenario's pin on disk: ``<name>.pin.json``, beside the scenario file (ARCHITECTURE §8).

Written only by ``semis pin``, so semis never rewrites a scenario file and a re-pin is
reviewed as the diff of one file. ``pin.py`` builds, digests and compares pins as
mappings; reading and writing them is here.
"""

import json
from pathlib import Path

from fraiseql_semis.errors import ScenarioError
from fraiseql_semis.pin import SchemaPin

SUFFIX = ".pin.json"


def path_for(scenario_file: Path, name: str) -> Path:
    """Where the scenario *name*, in *scenario_file*, keeps its pin: beside the file, named
    after the scenario. A name that would leave the file's directory is refused."""
    path = scenario_file.parent / f"{name}{SUFFIX}"
    if path.parent != scenario_file.parent or Path(name).name != name:
        raise ScenarioError(
            f"scenario {name}: its pin would be written outside {scenario_file.parent}",
            resolution_hint="Use letters, digits, ., - and _: the name becomes file names.",
        )
    return path


def read(path: Path, *, scenario: str) -> SchemaPin | None:
    """The pin kept at *path*, or ``None`` when there is no such file: the scenario is
    unpinned. A file that does not read as the pin a ``semis pin`` wrote is refused."""
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text())
    except ValueError, RecursionError:
        document = None
    return SchemaPin.from_document(document, scenario=scenario)


def write(path: Path, pin: SchemaPin) -> None:
    """*pin*, written to *path* whole: keys sorted, indented, ending in a newline, so a
    re-pin reads as a diff."""
    path.write_text(json.dumps(pin.to_document(), indent=2, sort_keys=True) + "\n")
