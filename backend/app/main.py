import asyncio
import json
import logging
import re
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# Add local vendor packages to sys.path
vendor_dir = Path(__file__).resolve().parent.parent / "vendor"
if vendor_dir.exists() and str(vendor_dir) not in sys.path:
    sys.path.insert(0, str(vendor_dir))

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from app.config import settings
from app.modules.auth.api import router as auth_router
from app.modules.auth.nonce_service import run_periodic_rate_limit_cleanup
from app.modules.invoice_brain.api import router as invoice_brain_router
from app.modules.reconciliation.api import router as reconciliation_router
from app.modules.gstr3b.api import router as gstr3b_router
from app.modules.credit_builder.api import router as credit_builder_router
from app.modules.scheme_navigator.api import router as scheme_navigator_router
from app.modules.ai_assistant.api import router as ai_assistant_router

logger = logging.getLogger("myca.api")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan context manager.
    Verifies GSTR-3B trust anchor alignment on startup in production/staging and
    manages periodic background rate-limit cleanup.
    """
    env_norm = (settings.ENVIRONMENT or "").strip().lower()
    if env_norm in ("production", "staging"):
        from app.database import SessionLocal
        from app.modules.gstr3b.service import verify_gstr3b_trust_anchor_sync

        db = SessionLocal()
        try:
            if not verify_gstr3b_trust_anchor_sync(db):
                raise RuntimeError(
                    "CRITICAL SECURITY ERROR: GSTR3B_REVIEW_ATTESTATION_KEY does not match database trust anchor."
                )
        finally:
            db.close()

    stop_event = asyncio.Event()
    cleanup_task = asyncio.create_task(
        run_periodic_rate_limit_cleanup(
            interval_seconds=getattr(settings, "RATE_LIMIT_CLEANUP_INTERVAL_SECONDS", 3600),
            retention_hours=getattr(settings, "RATE_LIMIT_RETENTION_HOURS", 1),
            stop_event=stop_event,
        )
    )
    app.state.rate_limit_cleanup_task = cleanup_task
    app.state.rate_limit_cleanup_stop_event = stop_event
    try:
        yield
    finally:
        stop_event.set()
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Autonomous Financial Intelligence Platform for Indian MSMEs",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-User-Id", "X-Org-Id", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def attach_request_id_and_security_headers(request: Request, call_next):
    raw_req_id = request.headers.get("X-Request-ID", "").strip()
    request_id = raw_req_id if _REQUEST_ID_PATTERN.match(raw_req_id) else str(uuid.uuid4())
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.path.startswith(f"{settings.API_V1_STR}/auth"):
        response.headers["Cache-Control"] = "no-store"

    logger.info(
        json.dumps(
            {
                "event": "http_request",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
            }
        )
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    req_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    logger.error("Unhandled internal error [request_id=%s]: %s", req_id, exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "INTERNAL_SERVER_ERROR: An unexpected error occurred. Reference the request ID for support.",
            "request_id": req_id,
        },
        headers={"X-Request-ID": req_id},
    )


# Mount Routers
app.include_router(auth_router, prefix=settings.API_V1_STR)
app.include_router(invoice_brain_router, prefix=settings.API_V1_STR)
app.include_router(reconciliation_router, prefix=settings.API_V1_STR)
app.include_router(gstr3b_router, prefix=settings.API_V1_STR)
app.include_router(credit_builder_router, prefix=settings.API_V1_STR)
app.include_router(scheme_navigator_router, prefix=settings.API_V1_STR)
app.include_router(ai_assistant_router, prefix=settings.API_V1_STR)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION,
    }


@app.get("/ready")
def readiness_check():
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1;")).scalar()
        alembic_rev = db.execute(text("SELECT version_num FROM public.alembic_version LIMIT 1;")).scalar()
        if not alembic_rev:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="DATABASE_NOT_MIGRATED: alembic_version missing.",
            )
        return {
            "status": "ready",
            "alembic_revision": str(alembic_rev),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Readiness check failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DEPENDENCY_UNREADY: PostgreSQL readiness check failed.",
        ) from exc
    finally:
        db.close()


@app.get("/")
def root():
    return {"message": "Welcome to MyCA API"}
