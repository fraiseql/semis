"""semis' own failures, in the shape of confiture's: message, code, exit code, hint.

Confiture's exceptions propagate unwrapped (D11); these are raised only for what semis
itself refuses.
"""

from collections.abc import Mapping, Set


class SemisError(Exception):
    """A refusal semis makes. Shaped like ``ConfiturError`` without being one."""

    error_code = "SEMIS_000"
    exit_code = 1
    default_hint: str | None = None
    """What to do, for a refusal of this kind whose raiser names nothing more precise."""

    def __init__(self, message: str, *, resolution_hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.resolution_hint = resolution_hint or self.default_hint

    def __str__(self) -> str:
        if self.resolution_hint is None:
            return self.message
        return f"{self.message}\nHint: {self.resolution_hint}"


class CodeRegistryError(SemisError):
    """A table with no code, or two tables with the same one."""

    error_code = "SEMIS_CODES_001"


class RowContractError(SemisError):
    """A row refused by the row contract: names the table, the column and the fact (D4),
    and the scenario, when a scenario's run drew it."""

    error_code = "SEMIS_ROWS_001"


class ResolutionError(SemisError):
    """No parent row to point a foreign key at: names the child column and the parent."""

    error_code = "SEMIS_RESOLVE_001"


class ScenarioError(SemisError):
    """A scenario that does not load, names a mode that is not one (D3), or cannot be
    written in it: a prep-seed table without its staging twin (D26)."""

    error_code = "SEMIS_SCENARIO_001"
    default_hint = "The scenario examples in the README show every key a scenario takes."


class AlreadyAppliedError(SemisError):
    """A scenario applied to a database that already holds its rows: refused before a
    row is written, naming the reset. A scenario applies once, to a reset database."""

    error_code = "SEMIS_APPLY_001"


class ResetBlockedError(SemisError):
    """A reset of a scenario whose rows a row it did not write points at: deleting them
    would break that key, or reach that row through a cascade, so none is deleted."""

    error_code = "SEMIS_RESET_001"


class ResetScopeError(SemisError):
    """A reset of a scenario one of whose tables has no uuid natural id: the scenario's
    rows there cannot be told from others, so none is deleted."""

    error_code = "SEMIS_RESET_002"


class UnreachableDatabaseError(SemisError):
    """A database semis was told to connect to, which did not answer."""

    error_code = "SEMIS_DATABASE_001"


class SchemaNotBuiltError(SemisError):
    """A database that answered, but holds no table the run reads: its schema is not
    built there."""

    error_code = "SEMIS_DATABASE_002"


class ProjectError(SemisError):
    """A project file that does not load, or a command with no project to read."""

    error_code = "SEMIS_PROJECT_001"
    default_hint = "The semis.yaml example in the README shows every key a project takes."


class PinError(SemisError):
    """A scenario replayed against a schema whose facts no longer match its pin (D5).

    Carries both digests, and what moved between the facts the pin kept and the
    schema's.
    """

    error_code = "SEMIS_PIN_001"

    def __init__(
        self,
        message: str,
        *,
        recorded: str,
        current: str,
        changes: tuple[str, ...] = (),
        resolution_hint: str | None = None,
    ) -> None:
        super().__init__(message, resolution_hint=resolution_hint)
        self.recorded = recorded
        self.current = current
        self.changes = changes


class IncomparablePinError(PinError):
    """A pin taken from one kind of source, replayed against the other (§8).

    DDL and a live database spell the same schema differently, so the digests cannot be
    compared at all: this is not a report that the schema moved.
    """

    error_code = "SEMIS_PIN_002"


def refuse_unknown(
    where: str, data: Mapping[object, object], known: Set[str], *, error: type[SemisError]
) -> None:
    """Refuse *data*, a mapping read at *where*, with *error* when it holds a key not in
    *known*, naming every such key and listing the known ones."""
    unknown = sorted(str(key) for key in set(data) - known)
    if unknown:
        raise error(
            f"{where}: unknown key {', '.join(unknown)}",
            resolution_hint=f"Known keys: {', '.join(sorted(known))}.",
        )
