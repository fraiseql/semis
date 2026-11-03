"""A CHECK read as the list of values it allows, when it is one.

``role IN ('a', 'b')`` and its stored form, ``role = ANY (ARRAY['a'::text, 'b'::text])``,
allow exactly the values listed: semis draws a column so constrained from its list. Any
other expression is not read, and never evaluated: PostgreSQL judges it (ARCHITECTURE
§11).

Pure: an expression in, values out.
"""

import re
from decimal import Decimal

__all__ = ["value_list"]

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<string>'(?:[^']|'')*')
      | (?P<quoted>"(?:[^"]|"")*")
      | (?P<number>\d+(?:\.\d+)?)
      | (?P<word>[A-Za-z_][A-Za-z0-9_$]*)
      | (?P<cast>::)
      | (?P<mark>.)
    )""",
    re.VERBOSE | re.DOTALL,
)
# The words a type spelled in SQL's own grammar continues with: `character varying`,
# `double precision`, `timestamp with time zone`.
_TYPE_WORDS = frozenset({"varying", "precision", "with", "without", "time", "zone"})

type _Token = tuple[str, str]


class _NotAList(Exception):
    """The expression is not a plain list of values on the column."""


def value_list(column: str, expression: str) -> tuple[object, ...] | None:
    """The values *expression* allows *column*, in its order, when it is a plain list:
    ``column IN (…)`` or ``column = ANY (ARRAY[…])``, the column and each value maybe
    cast. ``None`` for any other expression. A string is a ``str``, a number an ``int``
    or a ``Decimal``."""
    try:
        return _Parser(_tokens(expression)).allowed(column)
    except _NotAList:
        return None


def _tokens(expression: str) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _TOKEN.finditer(expression.rstrip()):
        kind = str(match.lastgroup)
        tokens.append((kind, match.group(kind)))
    return tokens


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._at = 0

    def allowed(self, column: str) -> tuple[object, ...]:
        self._strip_parentheses()
        if self._subject() != column:
            raise _NotAList
        if self._word("in"):
            values = self._items("(", ")")
        else:
            self._expect("mark", "=")
            self._expect("word", "any")
            self._expect("mark", "(")
            values = self._array()
            self._expect("mark", ")")
        if self._at != len(self._tokens):
            raise _NotAList
        return values

    def _strip_parentheses(self) -> None:
        while (
            self._tokens
            and self._tokens[0] == ("mark", "(")
            and self._closing(0) == len(self._tokens) - 1
        ):
            self._tokens = self._tokens[1:-1]

    def _closing(self, opening: int) -> int | None:
        depth = 0
        for at in range(opening, len(self._tokens)):
            mark = self._tokens[at]
            depth += {("mark", "("): 1, ("mark", ")"): -1}.get(mark, 0)
            if depth == 0:
                return at
        return None

    def _subject(self) -> str:
        """The column an expression constrains, its casts dropped: an unquoted name
        folded to lower case, as PostgreSQL folds it."""
        if self._word("cast"):
            self._expect("mark", "(")
            name = self._subject()
            self._cast_to()
        elif self._mark("("):
            name = self._subject()
            self._expect("mark", ")")
        else:
            kind, text = self._next()
            if kind == "quoted":
                name = text[1:-1].replace('""', '"')
            elif kind == "word" and text.lower() not in {"cast", "array"}:
                name = text.lower()
            else:
                raise _NotAList
        self._casts()
        return name

    def _array(self) -> tuple[object, ...]:
        if self._word("cast"):
            self._expect("mark", "(")
            values = self._array()
            self._cast_to()
        elif self._mark("("):
            values = self._array()
            self._expect("mark", ")")
        else:
            self._expect("word", "array")
            values = self._items("[", "]")
        self._casts()
        return values

    def _items(self, opening: str, closing: str) -> tuple[object, ...]:
        self._expect("mark", opening)
        values = [self._item()]
        while self._mark(","):
            values.append(self._item())
        self._expect("mark", closing)
        return tuple(values)

    def _item(self) -> object:
        if self._word("cast"):
            self._expect("mark", "(")
            value = self._item()
            self._cast_to()
        elif self._mark("("):
            value = self._item()
            self._expect("mark", ")")
        else:
            value = self._literal()
        self._casts()
        return value

    def _literal(self) -> object:
        sign = "-" if self._mark("-") else ""
        kind, text = self._next()
        if kind == "number":
            return Decimal(sign + text) if "." in text else int(sign + text)
        if kind == "string" and not sign:
            return text[1:-1].replace("''", "'")
        raise _NotAList

    def _cast_to(self) -> None:
        """The rest of a ``CAST(… AS type)``: its type, then its closing parenthesis."""
        self._expect("word", "as")
        self._type()
        self._expect("mark", ")")

    def _casts(self) -> None:
        while self._peek() == ("cast", "::"):
            self._at += 1
            self._type()

    def _type(self) -> None:
        """A type name: dotted words, a modifier in parentheses, array brackets."""
        self._expect_kind("word")
        while True:
            token = self._peek()
            if token == ("mark", "."):
                self._at += 1
                self._expect_kind("word")
            elif token is not None and token[0] == "word" and token[1].lower() in _TYPE_WORDS:
                self._at += 1
            else:
                break
        if self._peek() == ("mark", "("):
            closing = self._closing(self._at)
            if closing is None:
                raise _NotAList
            self._at = closing + 1
        while self._mark("["):
            self._expect("mark", "]")

    def _peek(self) -> _Token | None:
        return self._tokens[self._at] if self._at < len(self._tokens) else None

    def _next(self) -> _Token:
        token = self._peek()
        if token is None:
            raise _NotAList
        self._at += 1
        return token

    def _mark(self, mark: str) -> bool:
        if self._peek() == ("mark", mark):
            self._at += 1
            return True
        return False

    def _word(self, word: str) -> bool:
        token = self._peek()
        if token is not None and token[0] == "word" and token[1].lower() == word:
            self._at += 1
            return True
        return False

    def _expect(self, kind: str, text: str) -> None:
        if not (self._word(text) if kind == "word" else self._mark(text)):
            raise _NotAList

    def _expect_kind(self, kind: str) -> None:
        if self._next()[0] != kind:
            raise _NotAList
