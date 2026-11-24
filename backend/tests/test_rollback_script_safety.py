"""Unit tests for migration rollback verification safety guard."""

import pytest
from scripts.verify_migration_rollback_state import assert_disposable_database_environment


def test_rollback_refuses_empty_db_admin_url():
    """Proves rollback verification aborts immediately if DB_ADMIN_URL is unset or empty."""
    with pytest.raises(RuntimeError) as excinfo:
        assert_disposable_database_environment("")
    assert "DB_ADMIN_URL environment variable is required and cannot be empty" in str(excinfo.value)


@pytest.mark.parametrize("env_val", ["production", "staging", "development", "unknown", ""])
def test_rollback_refuses_non_test_or_ci_environment(monkeypatch, env_val):
    """Proves rollback verification aborts if ENVIRONMENT is not strictly 'test' or 'ci'."""
    monkeypatch.setenv("ENVIRONMENT", env_val)
    monkeypatch.setenv("ALLOW_DISPOSABLE_DATABASE_ROLLBACK", "true")
    with pytest.raises(RuntimeError) as excinfo:
        assert_disposable_database_environment("postgresql://postgres@localhost:5432/myca_test")
    assert "strictly restricted to designated 'test' or 'ci' environments" in str(excinfo.value)


@pytest.mark.parametrize("flag_value", ["", "false", "0", "no", None])
def test_rollback_refuses_without_explicit_disposable_marker(monkeypatch, flag_value):
    """Proves rollback verification aborts if ALLOW_DISPOSABLE_DATABASE_ROLLBACK is not explicitly true."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    if flag_value is None:
        monkeypatch.delenv("ALLOW_DISPOSABLE_DATABASE_ROLLBACK", raising=False)
    else:
        monkeypatch.setenv("ALLOW_DISPOSABLE_DATABASE_ROLLBACK", flag_value)

    with pytest.raises(RuntimeError) as excinfo:
        assert_disposable_database_environment("postgresql://postgres@localhost:5432/myca_test")
    assert "ALLOW_DISPOSABLE_DATABASE_ROLLBACK=true" in str(excinfo.value)


@pytest.mark.parametrize("protected_dbname", ["myca", "postgres", "template1", "development", "myca_dev", "app", "main"])
def test_rollback_refuses_protected_database_names(monkeypatch, protected_dbname):
    """Proves rollback verification strictly prohibits persistent or development databases."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("ALLOW_DISPOSABLE_DATABASE_ROLLBACK", "true")

    with pytest.raises(RuntimeError) as excinfo:
        assert_disposable_database_environment(f"postgresql://postgres@localhost:5432/{protected_dbname}")
    assert "protected persistent database name" in str(excinfo.value)


@pytest.mark.parametrize("prod_url", [
    "postgresql://postgres:secret@prod-db.internal:5432/myca_test",
    "postgresql://postgres:secret@db.rds.amazonaws.com:5432/myca_test",
    "postgresql://postgres:secret@localhost:5432/myca_production_test",
    "postgresql://postgres:secret@localhost:5432/live_test_db",
])
def test_rollback_refuses_production_or_remote_targets(monkeypatch, prod_url):
    """Proves rollback verification aborts if database URL targets a production or remote cloud host."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("ALLOW_DISPOSABLE_DATABASE_ROLLBACK", "true")

    with pytest.raises(RuntimeError) as excinfo:
        assert_disposable_database_environment(prod_url)
    assert "CRITICAL SAFETY ABORT" in str(excinfo.value)


@pytest.mark.parametrize("valid_disposable_url", [
    "postgresql://postgres@localhost:5432/myca_test",
    "postgresql://postgres@127.0.0.1:5432/myca_test_disposable",
    "postgresql://postgres@postgres:5432/tmp_rollback_db",
])
def test_rollback_allows_disposable_local_database(monkeypatch, valid_disposable_url):
    """Verifies that rollback verification proceeds cleanly on authorized local disposable test databases."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("ALLOW_DISPOSABLE_DATABASE_ROLLBACK", "true")
    # Should not raise any exception
    assert_disposable_database_environment(valid_disposable_url)
