"""SchemaPin: the digest a scenario records of the schema it was written against (§8).

The digest covers only the facts semis consumes, so a change that cannot alter a
generated row (an index, a comment) leaves it unmoved. It is tagged with its source
kind, because DDL and a live database spell the same schema differently. A pin keeps the
projection it digested, so a refusal names what moved whatever the source. Pure: facts
in, a digest out.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from typing import cast, get_args

from fraiseql_semis.errors import IncomparablePinError, PinError, ScenarioError
from fraiseql_semis.resolution import EXISTING_BY
from fraiseql_semis.schema import CONFITURE_VERSION, ColumnFacts, SchemaFacts, SourceKind


@dataclass(frozen=True)
class SchemaPin:
    """A digest of the facts semis reads for a scenario's tables, and how they were read."""

    source: SourceKind
    digest: str
    confiture: str
    taken: date
    facts: str | None = None
    """The file beside the scenario keeping the projection the digest was taken from."""
    recorded: Projection | None = field(default=None, compare=False, repr=False)
    """That projection, once read: what a refusal compares the schema's with."""

    @classmethod
    def of(
        cls,
        facts: SchemaFacts,
        tables: Iterable[str],
        *,
        twins: Iterable[str] = (),
        existing: Iterable[str] = (),
        taken: date | None = None,
    ) -> SchemaPin:
        """The pin of *tables* as *facts* reads them, walked in dependency order, of the
        staging *twins* a prep-seed run writes into, and of the keys of the *existing*
        tables a read-back run points at, keeping that projection."""
        projected = projection(facts, tables, twins, existing)
        return cls(
            source=facts.source_kind,
            digest=digest(projected),
            confiture=CONFITURE_VERSION,
            taken=taken or date.today(),
            recorded=projected,
        )

    def with_facts(self, facts: str, recorded: Projection | None = None) -> SchemaPin:
        """This pin, naming *facts* as the file keeping its projection, and holding the
        *recorded* projection read from it, when it was."""
        return replace(self, facts=facts, recorded=self.recorded if recorded is None else recorded)

    def to_mapping(self) -> dict[str, object]:
        """The ``schema_pin:`` block a scenario records (ARCHITECTURE §8)."""
        block: dict[str, object] = {
            "source": self.source,
            "digest": self.digest,
            "confiture": self.confiture,
            "taken": self.taken,
        }
        if self.facts is not None:
            block["facts"] = self.facts
        return block

    @classmethod
    def from_mapping(cls, block: Mapping[str, object], *, scenario: str) -> SchemaPin:
        """The pin a scenario's ``schema_pin:`` block records."""
        if "facts" not in block and set(_BLOCK_FIELDS) <= set(block) <= _0_1_0_KEYS:
            raise ScenarioError(
                f"scenario {scenario}: schema_pin was written by semis 0.1.0, and keeps no facts",
                resolution_hint=(
                    "Re-pin the scenario: delete its schema_pin: block, run it with -o <dir>, "
                    "then put the schema_pin.yaml written beside its seeds in the block's place "
                    f"and the {facts_file(scenario)} beside the scenario file."
                ),
            )
        unknown = sorted(set(block) - set(_BLOCK_KEYS))
        missing = sorted({*_BLOCK_FIELDS, "facts"} - set(block))
        if unknown or missing:
            raise ScenarioError(
                f"scenario {scenario}: schema_pin has {_listed(unknown, 'unknown key')}"
                f"{' and ' if unknown and missing else ''}{_listed(missing, 'no')}",
                resolution_hint="Copy the block a run wrote to schema_pin.yaml, unchanged.",
            )
        source, digested, confiture, taken, facts = (block[key] for key in _BLOCK_KEYS)
        if (
            source not in get_args(SourceKind)
            or not isinstance(digested, str)
            or not isinstance(confiture, str)
            or not isinstance(taken, date)
            or not isinstance(facts, str)
        ):
            raise ScenarioError(
                f"scenario {scenario}: schema_pin is malformed",
                resolution_hint="Copy the block a run wrote to schema_pin.yaml, unchanged.",
            )
        return cls(cast("SourceKind", source), digested, confiture, taken, facts)


def facts_file(scenario: str) -> str:
    """The file a run of *scenario* keeps its pin's facts in, beside ``schema_pin.yaml``:
    named after the scenario, so the scenarios of one directory each keep their own. A
    scenario's name is a file name, so it is used as written."""
    return f"{scenario}.facts.json"


_BLOCK_FIELDS = ("source", "digest", "confiture", "taken")
_BLOCK_KEYS = (*_BLOCK_FIELDS, "facts")
_0_1_0_KEYS = {*_BLOCK_FIELDS, "snapshot"}


def _listed(keys: list[str], label: str) -> str:
    return f"{label} {', '.join(keys)}" if keys else ""


Projection = list[dict[str, object]]
"""What semis reads of a scenario's tables and twins: what a pin digests and keeps."""


def digest(projected: Projection) -> str:
    """The digest of a projection: its canonical JSON, hashed."""
    encoded = json.dumps(projected, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def projection(
    facts: SchemaFacts,
    tables: Iterable[str],
    twins: Iterable[str] = (),
    existing: Iterable[str] = (),
) -> Projection:
    """Per table in dependency order, what semis reads of it, and nothing else; then each
    staging twin's columns, or ``None`` for a twin the schema lacks; then each existing
    table's keys, and the columns that name its rows."""
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
    for table in existing:
        keys = facts.keys_for(table)
        named = {keys.natural_id, EXISTING_BY}
        projected.append(
            {
                "existing": table,
                "surrogate_pk": keys.surrogate_pk,
                "natural_id": keys.natural_id,
                "columns": [_column(c) for c in facts.columns(table) or () if c.name in named],
            }
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


def describe_changes(recorded: Projection, current: Projection) -> tuple[str, ...]:
    """What moved from the *recorded* projection to the *current* one, a line per change.

    Each line names a table, a staging twin or a column, then what happened to it. The
    order is *current*'s, which is dependency order, then what only *recorded* holds.
    Equal projections describe no change; a change inside a projection always does.
    """
    before = {_entry(entry): entry for entry in recorded}
    after = {_entry(entry): entry for entry in current}
    lines: list[str] = []
    for name, entry in after.items():
        old = before.get(name)
        if old is None or (old["columns"] is None) != (entry["columns"] is None):
            gone = old is not None and entry["columns"] is None
            lines.append(f"{name}: {_kind(entry)} {'removed' if gone else 'added'}")
        else:
            lines.extend(_entry_changes(name, old, entry))
    lines.extend(
        f"{name}: {_kind(entry)} removed" for name, entry in before.items() if name not in after
    )
    return tuple(lines)


_KINDS = {"table": "table", "twin": "staging twin", "existing": "existing table"}


def _entry(entry: Mapping[str, object]) -> str:
    return next(str(entry[key]) for key in _KINDS if key in entry)


def _kind(entry: Mapping[str, object]) -> str:
    return next(kind for key, kind in _KINDS.items() if key in entry)


def _entry_changes(
    name: str, old: Mapping[str, object], new: Mapping[str, object]
) -> Iterable[str]:
    for role in ("surrogate_pk", "natural_id"):
        if old.get(role) != new.get(role):
            yield f"{name}: {role} {_shown(old.get(role))} → {_shown(new.get(role))}"
    before = {str(column["name"]): column for column in _columns(old)}
    after = {str(column["name"]): column for column in _columns(new)}
    for column, facts in after.items():
        if column not in before:
            yield f"{name}.{column}: column added"
            continue
        for fact, value in facts.items():
            if before[column].get(fact) != value:
                yield f"{name}.{column}: {fact} {_shown(before[column].get(fact))} → {_shown(value)}"
    for column in before:
        if column not in after:
            yield f"{name}.{column}: column removed"
    kept_before = [column for column in before if column in after]
    kept_after = [column for column in after if column in before]
    if kept_before != kept_after:
        yield f"{name}: column order {', '.join(kept_before)} → {', '.join(kept_after)}"


def _columns(entry: Mapping[str, object]) -> list[Mapping[str, object]]:
    return cast("list[Mapping[str, object]]", entry["columns"])


def _shown(value: object) -> str:
    """*value* as a reader writes it: text bare, anything else as JSON."""
    return value if isinstance(value, str) else json.dumps(value)


def verify(  # noqa: PLR0913 — three positionals; the scenario's and the run's by keyword
    recorded: SchemaPin | None,
    facts: SchemaFacts,
    tables: Iterable[str],
    *,
    scenario: str,
    twins: Iterable[str] = (),
    existing: Iterable[str] = (),
    no_pin: bool = False,
    pin_kept: bool = True,
) -> str:
    """Refuse a replay of *scenario* when *facts* no longer match its *recorded* pin.

    A refusal names what moved, comparing the projection the pin kept with the
    schema's. *no_pin* skips the check for this one run. *pin_kept* says whether this
    run keeps the pin it wrote, for the hint to name one. Returns one line saying what
    the check did, so a skipped or absent check is never silent.
    """
    if no_pin:
        return f"scenario {scenario}: the schema pin is not checked for this run (no_pin)"
    current = SchemaPin.of(facts, tables, twins=twins, existing=existing)
    if recorded is None:
        return f"scenario {scenario} is unpinned: its schema is not checked"
    if recorded.source != current.source:
        raise IncomparablePinError(
            f"scenario {scenario} was pinned against a {recorded.source} schema and this run "
            f"reads a {current.source} one; their digests cannot be compared",
            recorded=recorded.digest,
            current=current.digest,
            resolution_hint=(
                f"Read the schema from {recorded.source} as the pin was, or {_repin(pin_kept)}."
            ),
        )
    if recorded.digest != current.digest:
        changes = (
            None
            if recorded.recorded is None or current.recorded is None
            else describe_changes(recorded.recorded, current.recorded)
        )
        raise PinError(
            f"scenario {scenario} was pinned to {recorded.digest}, and the schema now reads "
            f"{current.digest}\n{_describe(changes)}",
            recorded=recorded.digest,
            current=current.digest,
            changes=changes or (),
            resolution_hint=(
                f"Review the changes, then {_repin(pin_kept)}, or pass --no-pin for one run."
            ),
        )
    return f"scenario {scenario} matches its schema pin ({current.source} {current.digest})"


def _repin(pin_kept: bool) -> str:
    if pin_kept:
        return "re-pin the scenario from the schema_pin.yaml this run wrote beside its seeds"
    return (
        "re-pin the scenario from the schema_pin.yaml `semis seeds -o <dir>` or `semis apply` "
        "writes beside its seeds"
    )


def _describe(changes: tuple[str, ...] | None) -> str:
    if changes is None:
        return "The pin keeps no facts, so what moved cannot be named."
    if not changes:
        return "The pin's facts match this schema; its digest was not taken from them."
    return "What moved:\n" + "\n".join(f"  {change}" for change in changes)
