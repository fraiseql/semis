"""Which views a scenario's rows reach, and what each holds (fraiseql/semis#9, D56).

A view is reached when it reads a table the scenario writes, directly or through other
views, materialized views or TVIEWs, as confiture's ``view_reads`` follows them; a
routine's body is not followed. A view is multi-tenant when it outputs the project's
tenant column, by confiture's ``view_columns``. The counts are ``readback``'s.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from fraiseql_semis import readback
from fraiseql_semis.errors import ProjectError
from fraiseql_semis.schema import Connection, ObjectRef, SchemaFacts, qualified

_TWO = 2  # fewer tenants than this, and a view cannot show one tenant's rows hidden
_PART = r"[A-Za-z_][A-Za-z0-9_$]*"
_SETTING = re.compile(rf"{_PART}(?:\.{_PART})+")
_CUSTOM_HINT = (
    "Name a custom setting, prefix.name, as the views read it with current_setting(): "
    "app.agent_role. A built-in setting such as role or search_path would change how "
    "the report reads, so semis sets none."
)
_NAME_HINT = (
    "Write the name prefix.name, each part a letter or _ followed by letters, digits, "
    "_ or $: app.agent_role."
)
_VALUE_HINT = (
    "Quote the value, as in app.level: '1': YAML reads on, yes, true and 1 unquoted as "
    "a boolean or a number, and a setting's value is a string."
)


@dataclass(frozen=True)
class ViewCount:
    """What one view holds: its *rows*, and, when it outputs the tenant column, how
    many distinct *tenants* they belong to; ``None`` otherwise. *kind* is confiture's:
    ``view``, ``matview`` or ``tview``."""

    view: str
    kind: str
    rows: int
    tenants: int | None

    @property
    def single_tenant(self) -> bool:
        """A multi-tenant view whose rows are fewer than two tenants': a test of tenant
        isolation over it has no other tenant's rows to hide."""
        return self.tenants is not None and self.tenants < _TWO

    @property
    def mark(self) -> str:
        """What the report says beside the count: ``no rows``, ``no tenant`` (rows whose
        tenant column is ``NULL``), ``one tenant``, or nothing."""
        if not self.rows:
            return "no rows"
        if self.tenants == 0:
            return "no tenant"
        if self.tenants == 1:
            return "one tenant"
        return ""


def session_variables(variables: object, where: str) -> dict[str, str]:
    """*variables*, the settings a report reads with, refused with ``ProjectError``,
    *where* first, unless each is a custom setting named ``prefix.name`` holding a
    string: PostgreSQL is never handed a name semis could have refused."""
    if not isinstance(variables, Mapping):
        raise ProjectError(
            f"{where} maps a setting's name to its value",
            resolution_hint="Write session_variables: {app.agent_role: manager}.",
        )
    for name, value in variables.items():
        if isinstance(name, str) and re.fullmatch(_PART, name):
            raise ProjectError(
                f"{where} {name} is not a custom setting", resolution_hint=_CUSTOM_HINT
            )
        if not isinstance(name, str) or not _SETTING.fullmatch(name):
            raise ProjectError(f"{where} {name} is not a setting name", resolution_hint=_NAME_HINT)
        if not isinstance(value, str):
            raise ProjectError(
                f"{where} {name} is {value!r}, not a string", resolution_hint=_VALUE_HINT
            )
    return dict(variables)


def _checked(given: Mapping[str, str] | None) -> dict[str, str]:
    """*given*, a Python caller's session variables, checked as ``semis.yaml``'s are."""
    return {} if given is None else session_variables(given, "session_variables:")


def reached(facts: SchemaFacts, tables: Iterable[str]) -> list[ObjectRef]:
    """The views that read one of *tables*, schema-qualified names the model holds, in
    the order of their names."""
    written = {ref.identity for table in tables if (ref := facts.ref(table)) is not None}
    return [view for view in facts.views() if facts.reads(view) & written]


def count(
    facts: SchemaFacts,
    tables: Iterable[str],
    *,
    tenant_column: str | None,
    connection: Connection,
    session_variables: Mapping[str, str] | None = None,
) -> tuple[ViewCount, ...]:
    """Each view *tables* reach, counted on *connection*: its rows, and its tenants
    when it outputs *tenant_column*. Without a tenant column, no view's columns are
    asked for. *session_variables* are set first, until the transaction ends, so a
    view scoped by one counts the rows its identity sees."""
    variables = _checked(session_variables)
    views = reached(facts, tables)
    readback.set_variables(connection, variables)
    counts: list[ViewCount] = []
    for view in views:
        column = tenant_column
        if column is not None and column not in facts.view_columns(view):
            column = None
        rows, tenants = readback.count_view(connection, view, column)
        counts.append(ViewCount(qualified(view), view.kind, rows, tenants))
    return tuple(counts)
