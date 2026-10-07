"""Suite-wide wiring: the database a test may use, and which tests are integration tests.

Everything under tests/integration/ is an integration test; a test elsewhere that needs
PostgreSQL marks itself. Without SEMIS_TEST_DATABASE_URL, every one of them skips, except
in CI, where the session fails: a broken URL must not turn the database tests green.
"""

import os
from collections.abc import Mapping
from pathlib import Path

import pytest

INTEGRATION = Path(__file__).parent / "integration"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if INTEGRATION in item.path.parents:
            item.add_marker(pytest.mark.integration)


def database_url_from(environment: Mapping[str, str]) -> str:
    """The test database *environment* names; a skip without one, or in CI a failure."""
    url = environment.get("SEMIS_TEST_DATABASE_URL")
    if not url:
        if environment.get("GITHUB_ACTIONS") == "true":
            pytest.fail("SEMIS_TEST_DATABASE_URL is not set, and CI must run the database tests")
        pytest.skip("SEMIS_TEST_DATABASE_URL is not set")
    return url


@pytest.fixture(scope="session")
def database_url() -> str:
    return database_url_from(os.environ)
