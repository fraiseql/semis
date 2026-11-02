"""The pseudo-locale's transform: a label made visibly not the first locale's."""

import math
import unicodedata
from importlib import import_module

import pytest

from fraiseql_semis.translation import PSEUDO_LETTERS, pseudo


def test_a_label_is_mapped_padded_and_bracketed() -> None:
    assert pseudo("Category") == "[Ḉḁṭḕḡṓṙẏ ···]"


@pytest.mark.parametrize("text", ["a", "ab", "abc", "Category", "Saint-Étienne", "x" * 40])
def test_the_text_grows_by_at_least_forty_percent(text: str) -> None:
    assert len(pseudo(text)) - 2 == math.ceil(1.4 * len(text))


def test_an_empty_label_stays_empty_between_its_brackets() -> None:
    assert pseudo("") == "[]"


def test_a_character_the_table_does_not_hold_passes_through() -> None:
    assert pseudo("ô-1é")[1:5] == "ô-1é"


@pytest.mark.parametrize(
    ("first", "second"),
    [("cote", "côte"), ("Etienne", "Étienne"), ("pate", "pâte"), ("Mull", "Müll")],
)
def test_accented_pairs_stay_apart(first: str, second: str) -> None:
    assert pseudo(first) != pseudo(second)


@pytest.mark.parametrize("locale", ["fr_FR", "de_DE"])
def test_distinct_words_give_distinct_pseudo_labels(locale: str) -> None:
    words = set(import_module(f"faker.providers.lorem.{locale}").Provider.word_list)
    assert len({pseudo(word) for word in words}) == len(words)


@pytest.mark.parametrize("length", [3, 5, 8, 13])
def test_a_label_cut_to_fit_keeps_both_brackets(length: int) -> None:
    fitted = pseudo("Category", length=length)
    assert (len(fitted), fitted[0], fitted[-1]) == (length, "[", "]")
    assert pseudo("Category", length=8) == "[Ḉḁṭḕḡṓ]"


def test_the_letter_table_holds_52_distinct_latin_extended_additional_letters() -> None:
    letters = list(PSEUDO_LETTERS.values())
    assert sorted(PSEUDO_LETTERS) == sorted("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    assert len(set(letters)) == 52
    assert all(
        0x1E00 <= ord(letter) <= 0x1EFF and unicodedata.category(letter)[0] == "L"
        for letter in letters
    )
