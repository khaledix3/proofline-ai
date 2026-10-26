import os

import pytest

import conftest


def test_test_file_loads_only_test_url(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "ROOT", tmp_path)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "development-sentinel")
    (tmp_path / ".env.test").write_text(
        "TEST_DATABASE_URL=postgresql+psycopg://localhost/proofline_test\n"
        "DATABASE_URL=must-not-be-loaded\nUNRELATED_SETTING=must-not-be-loaded\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("UNRELATED_SETTING", raising=False)
    assert conftest.load_test_database_url() == "postgresql+psycopg://localhost/proofline_test"
    assert os.environ["DATABASE_URL"] == "development-sentinel"
    assert "UNRELATED_SETTING" not in os.environ
    assert "TEST_DATABASE_URL" not in os.environ


def test_environment_overrides_test_file(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "ROOT", tmp_path)
    (tmp_path / ".env.test").write_text("TEST_DATABASE_URL=file-sentinel\n", encoding="utf-8")
    monkeypatch.setenv("TEST_DATABASE_URL", "environment-sentinel")
    assert conftest.load_test_database_url() == "environment-sentinel"
    monkeypatch.setenv("TEST_DATABASE_URL", "")
    assert conftest.load_test_database_url() == ""


def test_missing_test_file_does_not_fall_back_to_development(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "ROOT", tmp_path)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    (tmp_path / ".env").write_text("TEST_DATABASE_URL=must-not-be-loaded\n", encoding="utf-8")
    assert conftest.load_test_database_url() == ""


def test_test_file_does_not_expand_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "ROOT", tmp_path)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("EXAMPLE_VALUE", "expanded")
    (tmp_path / ".env.test").write_text("TEST_DATABASE_URL=literal-${EXAMPLE_VALUE}\n", encoding="utf-8")
    assert conftest.load_test_database_url() == "literal-${EXAMPLE_VALUE}"


@pytest.mark.parametrize("database", ["proofline_dev", "other_test", "production"])
def test_only_proofline_test_is_allowed(database):
    with pytest.raises(ValueError):
        conftest.require_test_database("postgresql+psycopg://localhost/" + database)


def test_proofline_test_is_allowed():
    url = conftest.require_test_database("postgresql+psycopg://localhost/proofline_test")
    assert url.database == "proofline_test"


def test_invalid_url_error_does_not_echo_input():
    with pytest.raises(ValueError) as error:
        conftest.require_test_database("invalid-example-sentinel")
    assert "invalid-example-sentinel" not in str(error.value)


def test_postgresql_connection_guard_blocks_development_before_network():
    from sqlalchemy import create_engine
    engine = create_engine("postgresql+psycopg://127.0.0.1/proofline_dev")
    try:
        with pytest.raises(RuntimeError, match="proofline_test"):
            engine.connect()
    finally:
        engine.dispose()
