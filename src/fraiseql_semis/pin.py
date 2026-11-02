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
from typing import NamedTuple, cast, get_args
from uuid import UUID

from fraiseql_semis.errors import IncomparablePinError, PinError, ScenarioError
from fraiseql_semis.resolution import EXISTING_BY
from fraiseql_semis.schema import CONFITURE_VERSION, ColumnFacts, SchemaFacts, SourceKind


@dataclass(frozen=True)
class PinnedRead:
    """The existing rows of *table* a prep-seed scenario points at, as ``semis pin`` read
    them: each row's *by* value, ``None`` when nothing names it, and its UUID. *where*
    names the rows read, in its order; ``None`` reads the whole table."""

    table: str
    by: str | None
    where: tuple[str, ...] | None
    rows: tuple[tuple[object, str], ...]

    @property
    def key(self) -> tuple[str, str | None, tuple[str, ...] | None]:
        """What makes two reads one: the table, the column it is read by, and the rows."""
        return self.table, self.by, self.where

    def to_document(self) -> dict[str, object]:
        return {
            "table": self.table,
            "by": self.by,
            "where": None if self.where is None else list(self.where),
            "rows": [list(row) for row in self.rows],
        }


class Merged(NamedTuple):
    """The reads a re-pin keeps, and what it found: rows *stale* (gone, or a named row's
    UUID changed), rows *added* to a whole-table read, reads the pin was *missing*."""

    reads: tuple[PinnedRead, ...]
    stale: tuple[str, ...]
    added: tuple[str, ...]
    missing: tuple[str, ...]


def merge_reads(previous: Iterable[PinnedRead] | None, current: Iterable[PinnedRead]) -> Merged:
    """The *current* reads as a pin keeps them, given the *previous* ones it kept.

    A whole-table read keeps its rows in the order first pinned, and appends the rows it
    gained in the order read; a read naming its rows takes them as read. Each line names
    the table.
    """
    before = {read.key: read for read in previous or ()}
    reads: list[PinnedRead] = []
    stale: list[str] = []
    added: list[str] = []
    missing: list[str] = []
    for read in current:
        old = before.get(read.key)
        if old is None:
            if previous is not None:
                missing.append(f"{read.table}: not pinned, by {read.by or 'its id'}")
            reads.append(read)
        elif read.where is not None:
            was = dict(old.rows)
            stale.extend(
                f"{read.table}: {value} {was[value]} → {uuid}"
                for value, uuid in read.rows
                if was.get(value, uuid) != uuid
            )
            reads.append(read)
        else:
            now = {uuid: value for value, uuid in read.rows}
            kept = [(now[uuid], uuid) for _, uuid in old.rows if uuid in now]
            known = {uuid for _, uuid in old.rows}
            new = [row for row in read.rows if row[1] not in known]
            stale.extend(
                f"{read.table}: {_named(value)}{uuid} is gone"
                for value, uuid in old.rows
                if uuid not in now
            )
            if new:
                added.append(f"{read.table}: {len(new)} row{'s' if len(new) > 1 else ''} added")
            reads.append(replace(read, rows=(*kept, *new)))
    return Merged(tuple(reads), tuple(stale), tuple(added), tuple(missing))


def _named(value: object) -> str:
    return "" if value is None else f"{value} "


@dataclass(frozen=True)
class SchemaPin:
    """A digest of the facts semis reads for a scenario's tables, and how they were read;
    for a prep-seed scenario, the existing rows it points at."""

    source: SourceKind
    digest: str
    confiture: str
    taken: date
    recorded: Projection | None = field(default=None, compare=False, repr=False)
    """That projection, once read: what a refusal compares the schema's with."""
    rows: tuple[PinnedRead, ...] | None = None
    """The existing rows a prep-seed run points at; ``None`` for a pin that records none."""

    @classmethod
    def of(  # noqa: PLR0913 — the facts and tables positionally; what else to project by keyword
        cls,
        facts: SchemaFacts,
        tables: Iterable[str],
        *,
        twins: Iterable[str] = (),
        existing: Iterable[str] = (),
        coded: Mapping[str, str] | None = None,
        taken: date | None = None,
    ) -> SchemaPin:
        """The pin of *tables* as *facts* reads them, walked in dependency order, of the
        staging *twins* a prep-seed run writes into, and of the keys of the *existing*
        tables a run points at, and of the locale tables in *coded*, each with the
        column its locales are found by, keeping that projection."""
        projected = projection(facts, tables, twins, existing, coded or {})
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
        document: dict[str, object] = {
            "confiture": self.confiture,
            "digest": self.digest,
            "facts": self.recorded,
            "source": self.source,
            "taken": self.taken.isoformat(),
        }
        if self.rows is not None:
            reads = [read.to_document() for read in self.rows]
            document["rows"] = {"digest": digest(reads), "reads": reads}
        return document

    @classmethod
    def from_document(cls, document: object, *, scenario: str) -> SchemaPin:
        """The pin a scenario's pin file keeps, refused unless it holds exactly the keys
        ``to_document`` writes, and facts its digest was taken from."""
        fields = document if isinstance(document, dict) else {}
        source, digested, confiture, taken, facts = (
            fields.get(key) for key in ("source", "digest", "confiture", "taken", "facts")
        )
        if (
            set(fields) - {"rows"} != _DOCUMENT_KEYS
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
            rows=None if "rows" not in fields else _rows(fields["rows"], scenario=scenario),
        )


_DOCUMENT_KEYS = {"confiture", "digest", "facts", "source", "taken"}
_REPIN_HINT = "Re-take it with semis pin on the scenario: it writes the file whole."


def _rows(section: object, *, scenario: str) -> tuple[PinnedRead, ...]:
    """A pin file's ``rows``: refused unless every read holds what ``to_document`` writes,
    and the reads are the ones their digest was taken from."""
    fields = section if isinstance(section, dict) else {}
    reads = fields.get("reads")
    parsed = (
        [_read(read) for read in reads]
        if set(fields) == {"digest", "reads"} and isinstance(reads, list)
        else [None]
    )
    if None in parsed:
        raise ScenarioError(
            f"scenario {scenario}: its pin file's rows are malformed", resolution_hint=_REPIN_HINT
        )
    if digest(cast("Projection", reads)) != fields["digest"]:
        raise ScenarioError(
            f"scenario {scenario}: its pin file's rows do not hold what their digest was "
            "taken from",
            resolution_hint=_REPIN_HINT,
        )
    return tuple(cast("list[PinnedRead]", parsed))


_ROW = 2  # a pinned row: the value that names it, and its UUID


def _read(read: object) -> PinnedRead | None:
    """One read of a pin file's ``rows``, or ``None`` when it is not one."""
    if not isinstance(read, dict) or set(read) != {"table", "by", "where", "rows"}:
        return None
    table, by, where, rows = read["table"], read["by"], read["where"], read["rows"]
    if (
        not isinstance(table, str)
        or not (by is None or isinstance(by, str))
        or not (
            where is None or (isinstance(where, list) and all(isinstance(w, str) for w in where))
        )
        or not isinstance(rows, list)
        or not all(isinstance(row, list) and len(row) == _ROW and _row(*row) for row in rows)
    ):
        return None
    return PinnedRead(
        table,
        by,
        None if where is None else tuple(where),
        tuple((row[0], row[1]) for row in rows),
    )


def _row(name: object, uuid: object) -> bool:
    """Whether a pinned row reads: a scalar that names it, and its UUID."""
    if isinstance(name, bool) or not (name is None or isinstance(name, str | int)):
        return False
    if not isinstance(uuid, str):
        return False
    try:
        UUID(uuid)
    except ValueError:
        return False
    return True


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
    coded: Mapping[str, str] | None = None,
) -> Projection:
    """Per table in dependency order, what semis reads of it, and nothing else; then each
    staging twin's columns, or ``None`` for a twin the schema lacks; then each existing
    table's keys, and the columns that name its rows; a locale table in *coded*, the
    column its locales are found by too, one entry even when it is also existing."""
    coded = coded or {}
    existing = list(existing)
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
    for table in [*existing, *(table for table in coded if table not in existing)]:
        keys = facts.keys_for(table)
        named = {keys.natural_id, EXISTING_BY, *([coded[table]] if table in coded else [])}
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
    coded: Mapping[str, str] | None = None,
    no_pin: bool = False,
) -> str:
    """Refuse a replay of *scenario* when *facts* no longer match its *recorded* pin.

    A refusal names what moved, comparing the projection the pin kept with the
    schema's. *no_pin* skips the check for this one run. Returns one line saying what
    the check did, so a skipped or absent check is never silent.
    """
    if no_pin:
        return f"scenario {scenario}: the schema pin is not checked for this run (no_pin)"
    current = SchemaPin.of(facts, tables, twins=twins, existing=existing, coded=coded)
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
