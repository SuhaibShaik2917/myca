import sys
from pathlib import Path

# Add local vendor packages to sys.path
vendor_dir = Path(__file__).resolve().parent.parent / "vendor"
if vendor_dir.exists() and str(vendor_dir) not in sys.path:
    sys.path.insert(0, str(vendor_dir))

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.modules.auth.api import router as auth_router
from app.modules.invoice_brain.api import router as invoice_brain_router
from app.modules.reconciliation.api import router as reconciliation_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Autonomous Financial Intelligence Platform for Indian MSMEs"
)

# CORS restricted to configured frontend origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-User-Id", "X-Org-Id"],
)

# Mount Routers
app.include_router(auth_router, prefix=settings.API_V1_STR)
app.include_router(invoice_brain_router, prefix=settings.API_V1_STR)
app.include_router(reconciliation_router, prefix=settings.API_V1_STR)

@app.get("/health")
def health_check():
    return {
        "status": "ok", 
        "project": settings.PROJECT_NAME, 
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT
    }

@app.get("/")
def root():
    return {"message": "Welcome to MyCA API"}
