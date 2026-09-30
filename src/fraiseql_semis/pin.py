"""SchemaPin: the digest a scenario records of the schema it was written against (§8).

The digest covers only the facts semis consumes, so a change that cannot alter a
generated row (an index, a comment) leaves it unmoved. It is tagged with its source
kind, because DDL and a live database spell the same schema differently. Pure: facts
in, a digest out.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from typing import cast, get_args

from fraiseql_semis.errors import IncomparablePinError, PinError, ScenarioError
from fraiseql_semis.schema import CONFITURE_VERSION, ColumnFacts, SchemaFacts, SourceKind


@dataclass(frozen=True)
class SchemaPin:
    """A digest of the facts semis reads for a scenario's tables, and how they were read."""

    source: SourceKind
    digest: str
    confiture: str
    taken: date
    snapshot: str | None = None
    """Where the DDL the pin was taken from was kept, when it was; not compared."""

    @classmethod
    def of(
        cls,
        facts: SchemaFacts,
        tables: Iterable[str],
        *,
        twins: Iterable[str] = (),
        taken: date | None = None,
    ) -> "SchemaPin":
        """The pin of *tables* as *facts* reads them, walked in dependency order, and of
        the staging *twins* a prep-seed run writes into."""
        encoded = json.dumps(
            projection(facts, tables, twins), sort_keys=True, separators=(",", ":")
        )
        return cls(
            source=facts.source_kind,
            digest=f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}",
            confiture=CONFITURE_VERSION,
            taken=taken or date.today(),
        )

    def with_snapshot(self, snapshot: str | None) -> "SchemaPin":
        """This pin, naming *snapshot* as the file holding its DDL."""
        return replace(self, snapshot=snapshot)

    def to_mapping(self) -> dict[str, object]:
        """The ``schema_pin:`` block a scenario records (ARCHITECTURE §8)."""
        block: dict[str, object] = {
            "source": self.source,
            "digest": self.digest,
            "confiture": self.confiture,
            "taken": self.taken,
        }
        if self.snapshot is not None:
            block["snapshot"] = self.snapshot
        return block

    @classmethod
    def from_mapping(cls, block: Mapping[str, object], *, scenario: str) -> "SchemaPin":
        """The pin a scenario's ``schema_pin:`` block records."""
        unknown = sorted(set(block) - _BLOCK_KEYS)
        missing = sorted({"source", "digest", "confiture", "taken"} - set(block))
        if unknown or missing:
            raise ScenarioError(
                f"scenario {scenario}: schema_pin has {_listed(unknown, 'unknown key')}"
                f"{' and ' if unknown and missing else ''}{_listed(missing, 'no')}",
                resolution_hint="Copy the block a run wrote to schema_pin.yaml, unchanged.",
            )
        source, digest, confiture, taken = (block[key] for key in _BLOCK_FIELDS)
        snapshot = block.get("snapshot")
        if (
            source not in get_args(SourceKind)
            or not isinstance(digest, str)
            or not isinstance(confiture, str)
            or not isinstance(taken, date)
            or not (snapshot is None or isinstance(snapshot, str))
        ):
            raise ScenarioError(
                f"scenario {scenario}: schema_pin is malformed",
                resolution_hint="Copy the block a run wrote to schema_pin.yaml, unchanged.",
            )
        return cls(cast("SourceKind", source), digest, confiture, taken, snapshot)


_BLOCK_FIELDS = ("source", "digest", "confiture", "taken")
_BLOCK_KEYS = {*_BLOCK_FIELDS, "snapshot"}


def _listed(keys: list[str], label: str) -> str:
    return f"{label} {', '.join(keys)}" if keys else ""


def projection(
    facts: SchemaFacts, tables: Iterable[str], twins: Iterable[str] = ()
) -> list[dict[str, object]]:
    """Per table in dependency order, what semis reads of it, and nothing else; then each
    staging twin's columns, or ``None`` for a twin the schema lacks."""
    projected: list[dict[str, object]] = []
    for ref in facts.insert_order(tables):
        table = facts.facts_for(ref.display)
        projected.append(
            {
                "table": ref.display,
                "surrogate_pk": table.surrogate_pk,
                "natural_id": table.natural_id,
                "columns": [_column(column) for column in table.columns],
            }
        )
    for twin in twins:
        columns = facts.columns(twin)
        projected.append(
            {"twin": twin, "columns": None if columns is None else [_column(c) for c in columns]}
        )
    return projected


def _column(column: ColumnFacts) -> dict[str, object]:
    reference = column.foreign_key
    return {
        "name": column.name,
        "type_key": column.type_key,
        "raw_sql_type": column.raw_sql_type,
        "not_null": column.not_null,
        "default": column.default,
        "unique": column.unique,
        "checks": list(column.checks),
        "enum_values": None if column.enum_values is None else list(column.enum_values),
        "foreign_key": None if reference is None else [reference.table.display, reference.column],
    }


def verify(  # noqa: PLR0913 — three positionals; the scenario's and the run's by keyword
    recorded: SchemaPin | None,
    facts: SchemaFacts,
    tables: Iterable[str],
    *,
    scenario: str,
    twins: Iterable[str] = (),
    snapshot: str | None = None,
    no_pin: bool = False,
) -> str:
    """Refuse a replay of *scenario* when *facts* no longer match its *recorded* pin.

    *snapshot* is the DDL the pin was taken from, when it kept one: a refusal then names
    what ``diff`` reports changed. *no_pin* skips the check for this one run. Returns
    one line saying what the check did, so a skipped or absent check is never silent.
    """
    if no_pin:
        return f"scenario {scenario}: the schema pin is not checked for this run (no_pin)"
    current = SchemaPin.of(facts, tables, twins=twins)
    if recorded is None:
        return f"scenario {scenario} is unpinned: its schema is not checked"
    if recorded.source != current.source:
        raise IncomparablePinError(
            f"scenario {scenario} was pinned against a {recorded.source} schema and this run "
            f"reads a {current.source} one; their digests cannot be compared",
            recorded=recorded.digest,
            current=current.digest,
            resolution_hint=(
                f"Read the schema from {recorded.source} as the pin was, or re-pin the "
                "scenario from the schema_pin.yaml this run wrote beside its seeds."
            ),
        )
    if recorded.digest != current.digest:
        changes = _changes(facts, snapshot)
        raise PinError(
            f"scenario {scenario} was pinned to {recorded.digest}, and the schema now reads "
            f"{current.digest}\n{_describe(changes)}",
            recorded=recorded.digest,
            current=current.digest,
            changes=changes or (),
            resolution_hint=(
                "Review the changes against the scenario, then re-pin it from the "
                "schema_pin.yaml this run wrote beside its seeds, or pass --no-pin for one run."
            ),
        )
    return f"scenario {scenario} matches its schema pin ({current.source} {current.digest})"


def _changes(facts: SchemaFacts, snapshot: str | None) -> tuple[str, ...] | None:
    return None if snapshot is None else facts.changes_since(snapshot)


def _describe(changes: tuple[str, ...] | None) -> str:
    if changes is None:
        return "The pin kept no DDL snapshot, so diff has nothing to compare with."
    if not changes:
        return "diff reports no change between the pinned snapshot and this schema."
    return "diff reports:\n" + "\n".join(f"  {change}" for change in changes)
