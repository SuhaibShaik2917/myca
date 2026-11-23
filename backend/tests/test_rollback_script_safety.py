"""Unit tests for migration rollback verification safety guard."""

import pytest
from scripts.verify_migration_rollback_state import assert_disposable_database_environment


def test_rollback_refuses_production_environment(monkeypatch):
    """Proves rollback verification aborts immediately if ENVIRONMENT is production."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOW_DISPOSABLE_DATABASE_ROLLBACK", "true")
    with pytest.raises(RuntimeError) as excinfo:
        assert_disposable_database_environment("postgresql://postgres@localhost:5432/myca")
    assert "CRITICAL SAFETY ABORT: verify_migration_rollback_state.py cannot run in 'production' environment" in str(excinfo.value)


def test_rollback_refuses_staging_environment(monkeypatch):
    """Proves rollback verification aborts immediately if ENVIRONMENT is staging."""
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("ALLOW_DISPOSABLE_DATABASE_ROLLBACK", "true")
    with pytest.raises(RuntimeError) as excinfo:
        assert_disposable_database_environment("postgresql://postgres@localhost:5432/myca")
    assert "CRITICAL SAFETY ABORT: verify_migration_rollback_state.py cannot run in 'staging' environment" in str(excinfo.value)


@pytest.mark.parametrize("flag_value", ["", "false", "0", "no", None])
def test_rollback_refuses_without_explicit_disposable_marker(monkeypatch, flag_value):
    """Proves rollback verification aborts if ALLOW_DISPOSABLE_DATABASE_ROLLBACK is not explicitly true."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    if flag_value is None:
        monkeypatch.delenv("ALLOW_DISPOSABLE_DATABASE_ROLLBACK", raising=False)
    else:
        monkeypatch.setenv("ALLOW_DISPOSABLE_DATABASE_ROLLBACK", flag_value)

    with pytest.raises(RuntimeError) as excinfo:
        assert_disposable_database_environment("postgresql://postgres@localhost:5432/myca")
    assert "ALLOW_DISPOSABLE_DATABASE_ROLLBACK=true" in str(excinfo.value)


@pytest.mark.parametrize("prod_url", [
    "postgresql://postgres:secret@prod-db.internal:5432/myca",
    "postgresql://postgres:secret@db.rds.amazonaws.com:5432/myca",
    "postgresql://postgres:secret@localhost:5432/myca_production",
    "postgresql://postgres:secret@localhost:5432/myca_live",
])
def test_rollback_refuses_production_database_targets(monkeypatch, prod_url):
    """Proves rollback verification aborts if database URL targets a production or remote cloud host."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("ALLOW_DISPOSABLE_DATABASE_ROLLBACK", "true")

    with pytest.raises(RuntimeError) as excinfo:
        assert_disposable_database_environment(prod_url)
    assert "CRITICAL SAFETY ABORT" in str(excinfo.value)


def test_rollback_allows_disposable_local_database(monkeypatch):
    """Verifies that rollback verification proceeds cleanly on local disposable test databases."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("ALLOW_DISPOSABLE_DATABASE_ROLLBACK", "true")
    # Should not raise any exception
    assert_disposable_database_environment("postgresql://postgres@localhost:5432/myca")
