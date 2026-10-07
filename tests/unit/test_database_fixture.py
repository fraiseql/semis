"""The database fixture: it skips locally without a URL, and fails in CI without one."""

import pytest

from tests.conftest import database_url_from


def test_a_url_given_is_the_url_used() -> None:
    assert database_url_from({"SEMIS_TEST_DATABASE_URL": "postgresql:///x"}) == "postgresql:///x"


def test_without_a_url_a_local_run_skips_the_database_tests() -> None:
    with pytest.raises(pytest.skip.Exception, match="SEMIS_TEST_DATABASE_URL is not set"):
        database_url_from({})


def test_without_a_url_ci_fails_rather_than_skips() -> None:
    with pytest.raises(
        pytest.fail.Exception,
        match="SEMIS_TEST_DATABASE_URL is not set, and CI must run the database tests",
    ):
        database_url_from({"GITHUB_ACTIONS": "true"})
