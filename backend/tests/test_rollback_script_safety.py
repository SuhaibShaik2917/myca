"""Unit tests for migration rollback verification safety guard."""

import pytest
from unittest.mock import MagicMock
from scripts.verify_migration_rollback_state import (
    assert_disposable_database_environment,
    assert_database_disposable_marker,
    assert_alembic_targets_same_database,
    DISPOSABLE_MARKER_TOKEN,
)


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


def test_rollback_refuses_missing_disposable_marker_table():
    """Proves rollback verification aborts if public.disposable_database_marker table does not exist."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.return_value = (False,)  # table does not exist

    with pytest.raises(RuntimeError) as excinfo:
        assert_database_disposable_marker(mock_conn)
    assert "Database marker table 'public.disposable_database_marker' does not exist" in str(excinfo.value)


def test_rollback_refuses_missing_or_invalid_disposable_marker_token():
    """Proves rollback verification aborts if disposable marker token is missing from table."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.side_effect = [(True,), None]  # table exists, but token query returns None

    with pytest.raises(RuntimeError) as excinfo:
        assert_database_disposable_marker(mock_conn)
    assert f"Mandatory disposable marker token '{DISPOSABLE_MARKER_TOKEN}' missing" in str(excinfo.value)


def test_rollback_accepts_valid_disposable_marker():
    """Verifies that rollback verification proceeds when valid disposable marker is confirmed."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.side_effect = [(True,), (DISPOSABLE_MARKER_TOKEN,)]

    # Should not raise any exception
    assert_database_disposable_marker(mock_conn)


def test_rollback_refuses_alembic_target_mismatch(monkeypatch):
    """Proves rollback verification aborts if Alembic DB target differs from active psycopg DB."""
    # 1. psycopg db_url mismatch with active_db
    with pytest.raises(RuntimeError) as excinfo:
        assert_alembic_targets_same_database("postgresql://postgres@localhost:5432/myca_test_a", "myca_test_b")
    assert "DB_ADMIN_URL path specifies database 'myca_test_a', but active connected PostgreSQL database is 'myca_test_b'" in str(excinfo.value)

    # 2. alembic environment variable mismatch
    monkeypatch.setenv("DB_ADMIN_URL", "postgresql://postgres@localhost:5432/myca_test_alembic")
    with pytest.raises(RuntimeError) as excinfo:
        assert_alembic_targets_same_database("postgresql://postgres@localhost:5432/myca_test", "myca_test")
    assert "Alembic environment target resolves to 'myca_test_alembic', which does not match active connected database 'myca_test'" in str(excinfo.value)


def test_rollback_accepts_matching_alembic_and_psycopg_targets(monkeypatch):
    """Verifies that rollback verification succeeds when Alembic and psycopg target the exact same database."""
    monkeypatch.setenv("DB_ADMIN_URL", "postgresql://postgres@localhost:5432/myca_test")
    assert_alembic_targets_same_database("postgresql://postgres@localhost:5432/myca_test", "myca_test")


def test_persistent_database_named_test_rejected_without_marker(monkeypatch):
    """
    Negative test: A persistent local database whose name happens to contain 'test'
    passes naming checks, but is strictly REJECTED when disposable marker is missing.
    """
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("ALLOW_DISPOSABLE_DATABASE_ROLLBACK", "true")
    # URL check passes because name contains 'test'
    assert_disposable_database_environment("postgresql://postgres@localhost:5432/myca_test")

    # But database-level check MUST abort because marker is missing
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.return_value = (False,)  # Marker table missing in persistent database
    with pytest.raises(RuntimeError) as excinfo:
        assert_database_disposable_marker(mock_conn)
    assert "A persistent database (even if named 'test') is not proved disposable without an explicit marker token" in str(excinfo.value)
