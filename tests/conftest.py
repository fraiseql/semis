"""Suite-wide wiring: the database a test may use, and which tests are integration tests.

Everything under tests/integration/ is an integration test; a test elsewhere that needs
PostgreSQL marks itself. Without SEMIS_TEST_DATABASE_URL, every one of them skips.
"""

import os
from pathlib import Path

import pytest

INTEGRATION = Path(__file__).parent / "integration"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if INTEGRATION in item.path.parents:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("SEMIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SEMIS_TEST_DATABASE_URL is not set")
    return url
