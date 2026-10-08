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
from dataclasses import dataclass, field
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

    def to_document(self) -> dict[str, object]:
        """The pin as a scenario's pin file keeps it: how the schema was read, the digest,
        and the projection it was taken from (ARCHITECTURE §8)."""
        return {
            "confiture": self.confiture,
            "digest": self.digest,
            "facts": self.recorded,
            "source": self.source,
            "taken": self.taken.isoformat(),
        }

    @classmethod
    def from_document(cls, document: object, *, scenario: str) -> SchemaPin:
        """The pin a scenario's pin file keeps, refused unless it holds exactly the keys
        ``to_document`` writes, and facts its digest was taken from."""
        fields = document if isinstance(document, dict) else {}
        source, digested, confiture, taken, facts = (
            fields.get(key) for key in ("source", "digest", "confiture", "taken", "facts")
        )
        if (
            set(fields) != _DOCUMENT_KEYS
            or source not in get_args(SourceKind)
            or not isinstance(digested, str)
            or not isinstance(confiture, str)
            or not isinstance(facts, list)
            or not isinstance(taken, str)
            or _date(taken) is None
        ):
            raise ScenarioError(
                f"scenario {scenario}: its pin file is malformed", resolution_hint=_REPIN_HINT
            )
        if digest(cast("Projection", facts)) != digested:
            raise ScenarioError(
                f"scenario {scenario}: its pin file does not hold the facts its digest was "
                "taken from",
                resolution_hint=_REPIN_HINT,
            )
        return cls(
            cast("SourceKind", source),
            digested,
            confiture,
            cast("date", _date(taken)),
            recorded=cast("Projection", facts),
        )


_DOCUMENT_KEYS = {"confiture", "digest", "facts", "source", "taken"}
_REPIN_HINT = "Re-take it with semis pin on the scenario: it writes the file whole."


def _date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


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
) -> str:
    """Refuse a replay of *scenario* when *facts* no longer match its *recorded* pin.

    A refusal names what moved, comparing the projection the pin kept with the
    schema's. *no_pin* skips the check for this one run. Returns one line saying what
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
                f"Read the schema from {recorded.source} as the pin was, or {_REPIN}."
            ),
        )
    if recorded.digest != current.digest:
        changes = describe_changes(recorded.recorded or [], current.recorded or [])
        raise PinError(
            f"scenario {scenario} was pinned to {recorded.digest}, and the schema now reads "
            f"{current.digest}\nWhat moved:\n" + "\n".join(f"  {change}" for change in changes),
            recorded=recorded.digest,
            current=current.digest,
            changes=changes,
            resolution_hint=f"Review the changes, then {_REPIN}, or pass --no-pin for one run.",
        )
    return f"scenario {scenario} matches its schema pin ({current.source} {current.digest})"


_REPIN = "accept them with semis pin on the scenario"
