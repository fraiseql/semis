"""A refusal names what to do: its own hint, or its kind's."""

import pytest

from fraiseql_semis.errors import ProjectError, ResolutionError, ScenarioError, SemisError


@pytest.mark.parametrize(
    ("error", "hint"),
    [
        (ProjectError, "semis.yaml example in the README"),
        (ScenarioError, "scenario examples in the README"),
    ],
)
def test_a_file_shape_refusal_points_at_the_documented_keys(
    error: type[SemisError], hint: str
) -> None:
    refused = error("scenarios: is a string")
    assert refused.resolution_hint is not None
    assert hint in refused.resolution_hint
    assert str(refused) == f"scenarios: is a string\nHint: {refused.resolution_hint}"


def test_a_sites_own_hint_wins() -> None:
    assert ProjectError("x", resolution_hint="do y").resolution_hint == "do y"


def test_a_kind_without_a_default_keeps_none() -> None:
    refused = ResolutionError("x")
    assert (refused.resolution_hint, str(refused)) == (None, "x")
