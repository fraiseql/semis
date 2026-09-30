"""The database-URL precedence semis takes from confiture (#152), as semis calls it.

``resolve_database_url`` lives in ``confiture.cli.dsn`` and ``Environment`` in
``confiture.config.environment``: neither is on ``confiture.platform``, so every behaviour
``schema.database_url`` relies on is pinned here, and a change upstream fails by name.
semis calls it two ways: with the project's ``env:`` as an explicit config, or with no
config at all.
"""

from pathlib import Path

import pytest
from confiture.cli.dsn import resolve_database_url
from confiture.config.environment import Environment
from confiture.platform import ConfigurationError

AMBIENT = "postgresql:///ambient"
CANONICAL = "postgresql:///canonical"


@pytest.fixture(autouse=True)
def no_urls_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CONFITURE_DATABASE_URL", raising=False)


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / "db" / "environments" / "development.yaml"
    path.parent.mkdir(parents=True)
    (tmp_path / "db" / "schema").mkdir()
    path.write_text(
        "name: development\ndatabase_url: postgresql:///from_env\ninclude_dirs: [db/schema]\n"
    )
    return path


def test_a_flag_wins_over_an_explicit_config(env_file: Path) -> None:
    assert resolve_database_url("postgresql:///flag", env_file, config_explicit=True) == (
        "postgresql:///flag"
    )


def test_a_flag_that_is_no_postgresql_url_is_config_003() -> None:
    with pytest.raises(ConfigurationError) as refused:
        resolve_database_url("mysql://x", None)
    assert (refused.value.error_code, refused.value.exit_code) == ("CONFIG_003", 5)


def test_a_config_003_refusal_does_not_echo_the_flags_password() -> None:
    with pytest.raises(ConfigurationError) as refused:
        resolve_database_url("mysql://semis:hunter2@host/db", None)
    assert "hunter2" not in str(refused.value)


def test_an_explicit_config_defers_to_its_file_over_the_ambient_url(
    env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", AMBIENT)
    assert resolve_database_url(None, env_file, config_explicit=True) is None


def test_an_explicit_config_beside_the_canonical_url_is_config_007(
    env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONFITURE_DATABASE_URL", CANONICAL)
    with pytest.raises(ConfigurationError) as refused:
        resolve_database_url(None, env_file, config_explicit=True)
    assert (refused.value.error_code, refused.value.exit_code) == ("CONFIG_007", 5)


def test_with_no_config_the_canonical_url_beats_the_ambient_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", AMBIENT)
    monkeypatch.setenv("CONFITURE_DATABASE_URL", CANONICAL)
    assert resolve_database_url(None, None, require_intentional_source=True) == CANONICAL


def test_with_no_config_the_ambient_url_serves_a_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", AMBIENT)
    assert resolve_database_url(None, None) == AMBIENT


def test_the_ambient_url_alone_is_refused_to_a_mutating_command_as_config_010(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", AMBIENT)
    with pytest.raises(ConfigurationError) as refused:
        resolve_database_url(None, None, require_intentional_source=True)
    assert (refused.value.error_code, refused.value.exit_code) == ("CONFIG_010", 5)


def test_no_source_at_all_is_none() -> None:
    assert resolve_database_url(None, None) is None


def test_an_environment_file_names_its_database_url(env_file: Path) -> None:
    project_dir = env_file.parents[2]
    assert Environment.load("development", project_dir).database_url == "postgresql:///from_env"


def test_a_missing_environment_file_is_config_001(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as refused:
        Environment.load("development", tmp_path)
    assert refused.value.error_code == "CONFIG_001"
