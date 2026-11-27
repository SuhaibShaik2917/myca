import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from jose import JWTError, jwt

from app.config import settings

ALGORITHM = "HS256"
TOKEN_ISSUER = "myca-api"
TOKEN_AUDIENCE = "myca-client"


def create_access_token(subject: str, org_id: str, expires_minutes: int | None = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: Dict[str, Any] = {
        "sub": subject,
        "org_id": org_id,
        "jti": secrets.token_hex(16),
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "typ": "access",
        "exp": expire,
        "iat": now,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[ALGORITHM],
            issuer=TOKEN_ISSUER,
            audience=TOKEN_AUDIENCE,
            options={
                "require_exp": True,
                "require_iat": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc

    if payload.get("typ") != "access" or not payload.get("sub") or not payload.get("org_id") or not payload.get("jti"):
        raise ValueError("Token is missing required claims")
    return payload
