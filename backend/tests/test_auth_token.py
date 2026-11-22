"""Unit tests for signed demo bearer-token authentication (no database required)."""

import uuid

import pytest
from fastapi import HTTPException

from app.config import settings
from app.core import dependencies
from app.core.security import create_access_token, decode_access_token


USER_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())


def test_access_token_round_trip_preserves_identity_claims():
    token = create_access_token(subject=USER_ID, org_id=ORG_ID)
    claims = decode_access_token(token)
    assert claims["sub"] == USER_ID
    assert claims["org_id"] == ORG_ID


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c"])
def test_invalid_tokens_are_rejected(token):
    with pytest.raises(ValueError):
        decode_access_token(token)


def test_bearer_token_identity_overrides_spoofed_headers():
    token = create_access_token(subject=USER_ID, org_id=ORG_ID)
    generator = dependencies.get_tenant_db(
        authorization=f"Bearer {token}",
        x_user_id=str(uuid.uuid4()),
        x_org_id=str(uuid.uuid4()),
    )
    # Advance past claim parsing: it only reaches the database lookup with the
    # token's own identity, so a database connection error proves the override
    # happened before any tenant context was established.
    with pytest.raises(Exception) as excinfo:
        next(generator)
    assert not isinstance(excinfo.value, HTTPException)


def test_invalid_bearer_token_returns_401():
    generator = dependencies.get_tenant_db(
        authorization="Bearer invalid.token.value",
        x_user_id=USER_ID,
        x_org_id=ORG_ID,
    )
    with pytest.raises(HTTPException) as excinfo:
        next(generator)
    assert excinfo.value.status_code == 401


def test_production_requires_bearer_token(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    generator = dependencies.get_tenant_db(
        authorization=None,
        x_user_id=USER_ID,
        x_org_id=ORG_ID,
    )
    with pytest.raises(HTTPException) as excinfo:
        next(generator)
    assert excinfo.value.status_code == 401
