"""Coverage: which entity rows keep a locale's label, and how many."""

import hashlib
import json
from fractions import Fraction

import pytest

from fraiseql_semis.translation import kept

TL = "catalog.tl_category"


def _kept(count: int, coverage: Fraction, code: str = "en-GB") -> frozenset[int]:
    return kept(range(1, count + 1), coverage, scenario=0x5008, table=TL, code=code)


@pytest.mark.parametrize(
    ("count", "coverage", "expected"),
    [
        (20, Fraction("0.8"), 16),
        (100, Fraction("0.29"), 29),
        (3, Fraction("0.3"), 0),
        (7, Fraction(0), 0),
        (7, Fraction(1), 7),
    ],
)
def test_coverage_keeps_the_floor_of_count_times_coverage(
    count: int, coverage: Fraction, expected: int
) -> None:
    assert len(_kept(count, coverage)) == expected


def test_the_same_rows_are_kept_on_every_call() -> None:
    assert _kept(50, Fraction("0.6")) == _kept(50, Fraction("0.6"))


def test_raising_coverage_only_adds_rows() -> None:
    assert _kept(50, Fraction("0.8")) <= _kept(50, Fraction("0.9"))


def test_each_locale_misses_its_own_rows() -> None:
    assert _kept(50, Fraction("0.8"), "en-GB") != _kept(50, Fraction("0.8"), "de-DE")


def test_rows_are_ranked_by_a_sha256_of_their_json() -> None:
    """[scenario id, translation table, locale code, entity sequence], canonical JSON."""

    def rank(sequence: int) -> bytes:
        encoded = json.dumps([0x5008, TL, "en-GB", sequence], separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).digest()

    expected = frozenset(sorted(range(1, 11), key=rank)[:5])
    assert _kept(10, Fraction(1, 2)) == expected == {1, 2, 4, 8, 10}
