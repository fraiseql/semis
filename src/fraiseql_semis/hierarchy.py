"""Hierarchies: a table whose foreign key points at itself.

Rows are numbered breadth-first. The first *roots* rows are roots, and every later row's
parent is an earlier row, *fan_out* children to a parent, so every level is full but the
last. A tree whose every row is a root needs no *fan_out*. Pure: a shape in, row numbers
out.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from fraiseql_semis.errors import ScenarioError


@dataclass(frozen=True)
class Hierarchy:
    """How a scenario shapes a self-referencing table.

    *parent* names the self-FK that builds the tree; *fan_out* is how many children each
    parent row has, ``None`` when no row has a parent; *path* names an ``ltree`` column
    read-back fills with ``pk_*`` labels, the parent's path then the row's own key.
    """

    parent: str
    roots: int
    fan_out: int | None = None
    path: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.parent, str) or not isinstance(self.path, str | None):
            raise ScenarioError(
                f"hierarchy on {self.parent!r}: parent and path names columns, by strings",
                resolution_hint="Name parent: and path: by their columns' names.",
            )
        for name in ("roots", "fan_out"):
            value = getattr(self, name)
            if name == "fan_out" and value is None:
                continue
            if type(value) is not int or value < 1:
                raise ScenarioError(
                    f"hierarchy on {self.parent}: {name} is {value!r}, not a whole number "
                    "of at least one",
                    resolution_hint="A tree has at least one root, and a parent at least one child.",
                )

    def levels(self, count: int) -> list[range]:
        """The rows of each level, roots first, for a table of *count* rows.

        No rows is one empty level, so the table is still walked once.
        """
        levels = [range(0, min(self.roots, count))]
        width = self.roots
        while levels[-1].stop < count:
            width *= self._children(count)
            start = levels[-1].stop
            levels.append(range(start, min(start + width, count)))
        return levels

    def parent_of(self, row: int) -> int | None:
        """The row *row* hangs from, by its 0-based number; ``None`` for a root."""
        if row < self.roots:
            return None
        return (row - self.roots) // self._children(row + 1)

    def require_fan_out(self, table: str, count: int) -> None:
        """Refuse a tree of *count* rows in *table* whose rows have a parent and no
        *fan_out* to hang them by."""
        self._children(count, table=table)

    def _children(self, count: int, *, table: str | None = None) -> int:
        """*fan_out*, which only a tree of more than *roots* rows reads: the one place a
        missing one is refused."""
        if self.fan_out is not None or count <= self.roots:
            return self.fan_out or 1
        with_parent = count - self.roots
        subject = f"{table}: hierarchy" if table else f"hierarchy on {self.parent}"
        raise ScenarioError(
            f"{subject} has no fan_out:, and {with_parent} of its {count} rows "
            f"{'has' if with_parent == 1 else 'have'} a parent",
            resolution_hint=(
                "Give fan_out:, how many children each parent row has; or roots: as many as "
                "count, for rows that are all roots."
            ),
        )


def refuse_path_in_prep_seed(table: str, hierarchy: Hierarchy) -> None:
    """Refuse a *hierarchy* path in prep-seed, which has no ``pk_*`` to label it with."""
    if hierarchy.path is not None:
        raise ScenarioError(
            f"prep-seed cannot set {table}.{hierarchy.path}: a path is labelled with pk_* "
            "keys, which exist only once the rows are promoted",
            resolution_hint=(
                "Drop path: and let the project recalculate its paths after promotion, "
                "or declare read-back."
            ),
        )


class Paths:
    """A hierarchy's ``ltree`` paths in ``pk_*`` labels, built level by level.

    A root's path is its own key; a child's is its parent's path, a dot and its own key
    — the format ``recalculate_tree_path`` writes. Keys exist only once a level is
    applied and read back, so this is read-back's alone.
    """

    def __init__(self, *, parent: str, natural_id: str) -> None:
        self._parent = parent
        self._natural_id = natural_id
        self._by_key: dict[object, str] = {}

    def extend(
        self, rows: Sequence[Mapping[str, object]], keys: Mapping[object, int]
    ) -> dict[object, str]:
        """Each of *rows*' path, by natural id; *keys* maps a natural id to its ``pk_*``.

        A row's parent column holds its parent's key, from an earlier level.
        """
        level: dict[object, str] = {}
        for row in rows:
            key = keys[row[self._natural_id]]
            parent = row[self._parent]
            path = str(key) if parent is None else f"{self._by_key[parent]}.{key}"
            self._by_key[key] = level[row[self._natural_id]] = path
        return level
