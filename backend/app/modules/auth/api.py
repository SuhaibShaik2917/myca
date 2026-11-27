import ipaddress
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, List

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status, Header
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.dependencies import get_tenant_db, validate_tenant_identity
from app.core.jwks import JWKSUnavailableError, default_jwks_client, validate_jwks_endpoint_url
from app.core.security import create_access_token
from app.database import SessionLocal, set_tenant_context, verify_and_resolve_tenant_membership
from app.modules.auth.idp_verifier import validate_idp_token, _resolve_trust_settings
from app.modules.auth.nonce_service import (
    consume_idp_login_nonce,
    create_idp_login_nonce,
    preverify_idp_auth_transaction,
    schedule_rate_limit_cleanup_if_due,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


class DemoTokenRequest(BaseModel):
    user_id: str
    org_id: str


class DemoTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    org_id: str
    role: Optional[str] = None
    expires_in: int


class IdpNonceRequest(BaseModel):
    org_id: str
    idp_provider: str
    state: str = Field(..., min_length=16, max_length=128, description="OIDC transaction state")
    code_challenge: str = Field(..., min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]{43,128}$", description="Base64url-encoded SHA-256 PKCE code challenge")


class IdpNonceResponse(BaseModel):
    nonce: str
    expires_in: int
    org_id: str
    idp_provider: str
    state: str
    code_challenge: str


class IdpCodeExchangeRequest(BaseModel):
    idp_provider: str
    org_id: str
    code: str = Field(..., min_length=4, max_length=2048, description="OIDC Authorization Code")
    state: str = Field(..., min_length=16, max_length=128, description="OIDC transaction state")
    code_verifier: str = Field(..., min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_.~-]{43,128}$", description="RFC 7636 PKCE code verifier (43-128 unreserved chars)")
    redirect_uri: str = Field(..., min_length=8, max_length=512, description="Exact redirect_uri used in authorization request")


def _auth_cookie_path() -> str:
    prefix = (getattr(settings, "API_V1_STR", "/api/v1") or "/api/v1").rstrip("/")
    return f"{prefix}/auth"


def _effective_env() -> str:
    raw_env = os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "")
    return str(raw_env).strip().lower() if raw_env else "development"


def is_demo_token_allowed() -> bool:
    """
    Determines if demo token generation is permitted.
    Disabled in production/staging; permitted in local/test ONLY when ALLOW_DEMO_TOKENS=true.
    """
    env = _effective_env()
    if env not in ("development", "test"):
        return False

    allow_flag = getattr(settings, "ALLOW_DEMO_TOKENS", False) or (
        os.getenv("ALLOW_DEMO_TOKENS", "").strip().lower() in ("true", "1", "yes")
    )
    return bool(allow_flag)


@router.post("/demo-token", response_model=DemoTokenResponse)
def issue_demo_token(payload: DemoTokenRequest) -> DemoTokenResponse:
    raw_env = os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "")
    env = str(raw_env).strip().lower() if raw_env else "unspecified"

    if env not in ("development", "test"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"DEMO_TOKEN_PROHIBITED: /auth/demo-token is strictly disabled in '{env}' environment. "
                   "Production and staging requests must authenticate via an authorized Identity Provider.",
        )

    if not is_demo_token_allowed():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="DEMO_TOKEN_DISABLED_BY_DEFAULT: /auth/demo-token is disabled by default. "
                   "It can only be enabled in designated local/test environments via explicit ALLOW_DEMO_TOKENS=true configuration.",
        )

    try:
        uuid.UUID(payload.user_id)
        uuid.UUID(payload.org_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user_id or org_id")

    db = SessionLocal()
    try:
        if not verify_and_resolve_tenant_membership(db, payload.user_id, payload.org_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User does not possess active membership in the requested organization",
            )
        raw_role = db.info.get("current_role")
        role = raw_role if isinstance(raw_role, str) else None
    finally:
        db.close()

    token = create_access_token(subject=payload.user_id, org_id=payload.org_id)
    return DemoTokenResponse(
        access_token=token,
        user_id=payload.user_id,
        org_id=payload.org_id,
        role=role,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/me")
def get_authenticated_context(
    db: Session = Depends(get_tenant_db),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    """Returns server-verified user identity, active role, organization PAN/name, GST registrations, and business profiles."""
    effective_org, effective_user = validate_tenant_identity(db, x_org_id, x_user_id)
    role = str(db.info.get("current_role") or "viewer")

    org_row = db.execute(
        text("SELECT id::text, legal_name, trade_name, pan FROM public.organizations WHERE id = :org;"),
        {"org": effective_org},
    ).fetchone()

    user_row = None
    if effective_user:
        user_row = db.execute(
            text("SELECT id::text, email, full_name FROM public.v_org_users WHERE id = :uid;"),
            {"uid": effective_user},
        ).fetchone()

    reg_rows = db.execute(
        text(
            """
            SELECT id::text, gstin, state_code, legal_name, trade_name, registration_type, is_primary
            FROM public.gst_registrations
            WHERE org_id = :org
            ORDER BY is_primary DESC, gstin ASC;
            """
        ),
        {"org": effective_org},
    ).fetchall()

    profile_rows = db.execute(
        text(
            """
            SELECT id::text, gstin
            FROM public.business_profiles
            WHERE org_id = :org
            ORDER BY updated_at DESC NULLS LAST, created_at ASC;
            """
        ),
        {"org": effective_org},
    ).fetchall()

    return {
        "user_id": effective_user,
        "org_id": effective_org,
        "role": role,
        "email": user_row[1] if user_row else None,
        "full_name": user_row[2] if user_row else None,
        "organization": {
            "id": org_row[0] if org_row else effective_org,
            "legal_name": org_row[1] if org_row else None,
            "trade_name": org_row[2] if org_row else None,
            "pan": org_row[3] if org_row else None,
        },
        "registrations": [
            {
                "id": r[0],
                "gstin": r[1],
                "state_code": r[2],
                "legal_name": r[3],
                "trade_name": r[4],
                "registration_type": r[5],
                "is_primary": bool(r[6]),
            }
            for r in reg_rows
        ],
        "profiles": [
            {"id": p[0], "gstin": p[1]}
            for p in profile_rows
        ],
    }


@router.post("/logout")
def logout_session(
    response: Response,
    db: Session = Depends(get_tenant_db),
):
    """Revokes current session JWT (jti) server-side and clears auth session cookie."""
    env = _effective_env()
    is_prod_like = env in ("production", "staging")
    jti = db.info.get("current_jti")
    org_id = db.info.get("current_org")
    user_id = db.info.get("current_user")
    exp_raw = db.info.get("current_exp")

    if jti and org_id and user_id:
        exp_dt = (
            datetime.fromtimestamp(int(exp_raw), tz=timezone.utc)
            if isinstance(exp_raw, (int, float))
            else datetime.now(timezone.utc)
        )
        db.execute(
            text(
                """
                INSERT INTO public.auth_revoked_tokens (jti, org_id, user_id, expires_at, reason)
                VALUES (:jti, :org_id, :user_id, :exp, 'logout')
                ON CONFLICT (jti) DO NOTHING;
                """
            ),
            {"jti": str(jti), "org_id": str(org_id), "user_id": str(user_id), "exp": exp_dt},
        )
        db.execute(
            text("""
                SELECT audit.log_audit_event(
                    CAST(:org AS uuid),
                    'auth_sessions',
                    CAST(:uid AS text),
                    'EXECUTE'::public.audit_action,
                    NULL,
                    '{"event": "SESSION_REVOKED"}'::jsonb,
                    '{"source": "auth_logout"}'::jsonb
                );
            """),
            {"org": str(org_id), "uid": str(user_id)},
        )
        db.commit()

    response.delete_cookie(
        key="myca_auth_session",
        path=_auth_cookie_path(),
        httponly=True,
        secure=is_prod_like,
        samesite="lax",
    )
    return {"status": "logged_out", "revoked": bool(jti)}


def is_trusted_proxy(ip_str: str, trusted_list: List[str]) -> bool:
    try:
        ip_obj = ipaddress.ip_address(ip_str.strip())
    except ValueError:
        return False

    for item in trusted_list:
        entry = item.strip()
        if not entry:
            continue
        try:
            if "/" in entry:
                if ip_obj in ipaddress.ip_network(entry, strict=False):
                    return True
            elif ip_obj == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


def extract_client_ip(request: Request) -> str:
    peer_raw = request.client.host if request.client and request.client.host else ""
    peer_str = peer_raw.strip()

    env_now = _effective_env()
    if peer_str in ("testclient", "localhost") and env_now not in ("production", "staging"):
        peer_str = "192.0.2.1"

    try:
        peer_obj = ipaddress.ip_address(peer_str)
        clean_peer = str(peer_obj)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"MALFORMED_PEER_IP: Direct connection host '{peer_raw}' is not a valid IP address.",
        )

    raw_trusted = getattr(settings, "TRUSTED_PROXIES", ["127.0.0.1", "::1"])
    if isinstance(raw_trusted, str):
        trusted_list = [p.strip() for p in raw_trusted.split(",") if p.strip()]
    elif isinstance(raw_trusted, (list, tuple, set)):
        trusted_list = [str(p).strip() for p in raw_trusted if str(p).strip()]
    else:
        trusted_list = ["127.0.0.1", "::1"]

    if not is_trusted_proxy(clean_peer, trusted_list):
        return clean_peer

    xff = request.headers.get("X-Forwarded-For")
    if not xff or not xff.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MISSING_FORWARDED_HEADER: Connection from trusted proxy omitted mandatory X-Forwarded-For header.",
        )

    raw_hops = [h.strip() for h in xff.split(",")]
    if not raw_hops or any(not h for h in raw_hops):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MALFORMED_FORWARDED_HEADER: X-Forwarded-For header contains empty or malformed hop entries.",
        )

    parsed_hops = []
    for hop in raw_hops:
        try:
            parsed_hops.append(str(ipaddress.ip_address(hop)))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"MALFORMED_FORWARDED_HEADER: Invalid IP address '{hop}' in X-Forwarded-For chain.",
            )

    client_ip = None
    for hop_str in reversed(parsed_hops):
        if not is_trusted_proxy(hop_str, trusted_list):
            client_ip = hop_str
            break

    if client_ip is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="UNTRUSTED_CLIENT_NOT_FOUND: All addresses in X-Forwarded-For chain are trusted proxies. Genuine client IP cannot be determined.",
        )

    return client_ip


@router.post("/idp-nonce", response_model=IdpNonceResponse)
def request_idp_login_nonce(
    payload: IdpNonceRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
) -> IdpNonceResponse:
    env = _effective_env()
    inbound_session_token = request.cookies.get("myca_auth_session")
    client_ip = extract_client_ip(request)

    db = SessionLocal()
    try:
        result = create_idp_login_nonce(
            db=db,
            org_id=payload.org_id,
            idp_provider=payload.idp_provider,
            state=payload.state,
            code_challenge=payload.code_challenge,
            session_token=inbound_session_token,
            client_ip=client_ip,
        )

        response.set_cookie(
            key="myca_auth_session",
            value=result["session_token"],
            max_age=300,
            httponly=True,
            secure=env in ("production", "staging"),
            samesite="lax",
            path=_auth_cookie_path(),
        )

        schedule_rate_limit_cleanup_if_due(background_tasks)

        return IdpNonceResponse(
            nonce=result["nonce"],
            expires_in=result["expires_in"],
            org_id=result["org_id"],
            idp_provider=result["idp_provider"],
            state=result["state"],
            code_challenge=result["code_challenge"],
        )
    finally:
        db.close()


def _finalize_idp_login(
    db,
    *,
    org_id: str,
    idp_provider: str,
    idp_token: str,
    expected_nonce: Optional[str],
    session_token: str,
    state: str,
    code_verifier: str,
    env: str,
    response: Response,
    background_tasks: BackgroundTasks,
) -> DemoTokenResponse:
    """Shared OIDC token verification, atomic nonce consumption, and tenant membership resolution."""
    verified_claims = validate_idp_token(
        idp_provider,
        idp_token,
        env=env,
        expected_nonce=expected_nonce,
    )
    verified_email = verified_claims["email"].strip().lower()
    token_nonce = verified_claims.get("nonce")
    if not token_nonce or not str(token_nonce).strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MISSING_IDP_NONCE: IdP token must contain an authentication transaction nonce claim.",
        )

    consume_idp_login_nonce(
        db=db,
        nonce=str(token_nonce).strip(),
        org_id=org_id,
        idp_provider=idp_provider,
        email=verified_email,
        session_token=session_token,
        state=state,
        code_verifier=code_verifier,
    )
    response.delete_cookie(
        key="myca_auth_session",
        path=_auth_cookie_path(),
        httponly=True,
        secure=env in ("production", "staging"),
        samesite="lax",
    )
    set_tenant_context(db, org_id=org_id, user_id=None)

    user_row = None
    try:
        # v_org_users security-barrier view enforces m.status='active' AND u.is_active=true
        user_row = db.execute(
            text("SELECT id, email, membership_status FROM public.v_org_users WHERE LOWER(email) = :email;"),
            {"email": verified_email},
        ).fetchone()
    except Exception as exc:
        logger.debug("User lookup via v_org_users failed cleanly: %s", exc)
        db.rollback()
        set_tenant_context(db, org_id=org_id, user_id=None)

    if not user_row:
        # Distinguish inactive user vs missing registration without bypassing active user enforcement
        try:
            g_row = db.execute(
                text("SELECT id, email, is_active FROM public.global_users WHERE LOWER(email) = :email;"),
                {"email": verified_email},
            ).fetchone()
            if g_row:
                if g_row[2] is not True:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="SUSPENDED_USER: User account is inactive or suspended.",
                    )
                user_row = g_row
        except HTTPException:
            raise
        except Exception as exc:
            logger.debug("User lookup via global_users encountered exception: %s", exc)
            db.rollback()
            set_tenant_context(db, org_id=org_id, user_id=None)

    if not user_row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"UNREGISTERED_IDP_USER: User with email '{verified_email}' is not registered in MyCA.",
        )

    user_id = str(user_row[0])
    status_val = user_row[2]
    is_active = status_val in (True, "active", "ACTIVE", "true")
    if not is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SUSPENDED_USER: User account is inactive or suspended.",
        )

    if not verify_and_resolve_tenant_membership(db, user_id, org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="TENANT_MEMBERSHIP_REQUIRED: User does not possess active membership or active account status.",
        )

    raw_role = db.info.get("current_role")
    role = raw_role if isinstance(raw_role, str) else None
    try:
        db.execute(
            text("""
                SELECT audit.log_audit_event(
                    CAST(:org AS uuid),
                    'auth_sessions',
                    CAST(:uid AS text),
                    'EXECUTE'::public.audit_action,
                    NULL,
                    '{"event": "AUTH_IDP_LOGIN", "method": "OIDC_AUTHENTICATED"}'::jsonb,
                    '{"source": "auth_idp"}'::jsonb
                );
            """),
            {"org": str(org_id), "uid": str(user_id)},
        )
        db.commit()
    except Exception:
        db.rollback()

    schedule_rate_limit_cleanup_if_due(background_tasks)
    token = create_access_token(subject=user_id, org_id=org_id)
    return DemoTokenResponse(
        access_token=token,
        user_id=user_id,
        org_id=org_id,
        role=role,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def _validate_and_resolve_code_exchange_endpoints(
    redirect_uri: str,
    env: str,
) -> tuple[str, str, Optional[str]]:
    is_prod_like = env in ("production", "staging")
    try:
        clean_redirect = validate_jwks_endpoint_url(redirect_uri, require_https=is_prod_like)
    except JWKSUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="INVALID_REDIRECT_URI: Invalid redirect URI.") from exc

    allowed_redirects = list(getattr(settings, "MYCA_IDP_REDIRECT_URIS", []) or [])
    env_redirects = os.getenv("MYCA_IDP_REDIRECT_URIS")
    if env_redirects:
        raw_r = env_redirects.strip()
        if raw_r.startswith("["):
            try:
                allowed_redirects = [str(u).strip() for u in json.loads(raw_r) if str(u).strip()]
            except (ValueError, TypeError, json.JSONDecodeError):
                allowed_redirects = [u.strip() for u in raw_r.split(",") if u.strip()]
        else:
            allowed_redirects = [u.strip() for u in raw_r.split(",") if u.strip()]

    if is_prod_like and not allowed_redirects:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IDP_INTEGRATION_UNAVAILABLE: MYCA_IDP_REDIRECT_URIS allowlist must be configured in production/staging.",
        )
    if allowed_redirects and clean_redirect not in allowed_redirects:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_REDIRECT_URI: Supplied redirect_uri is not in configured MYCA_IDP_REDIRECT_URIS allowlist.",
        )

    idp_issuer, idp_audience, _, _, discovery_url = _resolve_trust_settings()
    token_url = (os.getenv("MYCA_IDP_TOKEN_URL") or getattr(settings, "MYCA_IDP_TOKEN_URL", None) or "").strip() or None

    if not token_url and discovery_url:
        try:
            disc = default_jwks_client.resolve_discovery(
                discovery_url,
                require_https=is_prod_like,
                expected_issuer=idp_issuer,
                ttl_seconds=int(getattr(settings, "MYCA_IDP_JWKS_CACHE_TTL_SECONDS", 300) or 300),
                timeout_seconds=float(getattr(settings, "MYCA_IDP_JWKS_TIMEOUT_SECONDS", 5.0) or 5.0),
            )
            token_url = disc.get("token_endpoint")
        except JWKSUnavailableError as exc:
            logger.warning("OIDC discovery failed while resolving token_endpoint: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="IDP_INTEGRATION_UNAVAILABLE: Failed to resolve OIDC token endpoint.",
            ) from exc

    if not token_url or not idp_audience:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IDP_INTEGRATION_UNAVAILABLE: OIDC token_endpoint and client_id (MYCA_IDP_AUDIENCE) are required for authorization code exchange.",
        )

    try:
        safe_token_url = validate_jwks_endpoint_url(token_url, require_https=is_prod_like)
    except JWKSUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="IDP_INTEGRATION_UNAVAILABLE: Invalid IdP token endpoint.") from exc

    client_secret = (os.getenv("MYCA_IDP_CLIENT_SECRET") or getattr(settings, "MYCA_IDP_CLIENT_SECRET", None) or "").strip() or None
    return safe_token_url, idp_audience, client_secret


@router.post("/idp-code-exchange", response_model=DemoTokenResponse)
def exchange_idp_authorization_code(
    payload: IdpCodeExchangeRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
) -> DemoTokenResponse:
    try:
        uuid.UUID(payload.org_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org_id format")

    env = _effective_env()
    session_token = request.cookies.get("myca_auth_session")
    if not session_token or not str(session_token).strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MISSING_SESSION_COOKIE: Browser authentication requires myca_auth_session HttpOnly cookie.",
        )

    # Phase 1: Short DB pre-verification transaction (released before outbound HTTP call)
    db_pre = SessionLocal()
    try:
        expected_nonce = preverify_idp_auth_transaction(
            db=db_pre,
            org_id=payload.org_id,
            idp_provider=payload.idp_provider,
            state=payload.state,
            code_verifier=payload.code_verifier,
            session_token=session_token,
        )
    finally:
        db_pre.close()

    clean_redirect = payload.redirect_uri.strip()
    token_url, client_id, client_secret = _validate_and_resolve_code_exchange_endpoints(clean_redirect, env)

    form_data = {
        "grant_type": "authorization_code",
        "code": payload.code,
        "redirect_uri": clean_redirect,
        "client_id": client_id,
        "code_verifier": payload.code_verifier,
    }
    if client_secret:
        form_data["client_secret"] = client_secret

    timeout_s = float(getattr(settings, "MYCA_IDP_JWKS_TIMEOUT_SECONDS", 5.0) or 5.0)
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
            token_resp = client.post(token_url, data=form_data, headers={"Accept": "application/json"})
    except Exception as exc:
        logger.warning("IdP token endpoint network failure: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IDP_TOKEN_ENDPOINT_UNAVAILABLE: Unable to reach IdP token endpoint.",
        ) from exc

    if token_resp.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"IDP_TOKEN_ENDPOINT_UNAVAILABLE: IdP returned HTTP {token_resp.status_code}.",
        )
    if token_resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"IDP_CODE_EXCHANGE_FAILED: IdP rejected authorization code (HTTP {token_resp.status_code}).",
        )

    try:
        token_json = token_resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="INVALID_IDP_TOKEN_RESPONSE: Invalid JSON returned by IdP token endpoint.",
        ) from exc

    id_token = token_json.get("id_token") if isinstance(token_json, dict) else None
    if not id_token or not isinstance(id_token, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MISSING_ID_TOKEN: IdP token response omitted required OIDC 'id_token'.",
        )

    # Phase 2: Atomic verification, nonce consumption & session issuance
    db = SessionLocal()
    try:
        return _finalize_idp_login(
            db,
            org_id=payload.org_id,
            idp_provider=payload.idp_provider,
            idp_token=id_token,
            expected_nonce=expected_nonce,
            session_token=session_token,
            state=payload.state,
            code_verifier=payload.code_verifier,
            env=env,
            response=response,
            background_tasks=background_tasks,
        )
    finally:
        db.close()


@router.post("/idp-exchange", status_code=status.HTTP_410_GONE, deprecated=True)
def exchange_idp_token() -> None:
    """
    Permanently retired direct ID-token submission endpoint.
    Clients must use OIDC Authorization Code + PKCE via /api/v1/auth/idp-code-exchange.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "IDP_EXCHANGE_RETIRED: Direct ID-token submission via /api/v1/auth/idp-exchange "
            "is permanently retired. Use OIDC Authorization Code + PKCE via /api/v1/auth/idp-code-exchange."
        ),
    )
