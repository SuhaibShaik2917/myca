"""GSP / GSTN Filing Gateway Boundary (Sandbox vs Fail-Closed Live Integration)."""

import os
from dataclasses import dataclass
from typing import Any, Dict, Tuple

from app.config import settings
from app.modules.gstr3b.gsp_sandbox import GSPSandboxFilingError, simulate_gsp_gstn_filing


class GSPIntegrationUnavailableError(RuntimeError):
    """Raised when live GSTN/GSP mode is selected without authorized credentials/client."""


@dataclass(frozen=True)
class GSPGatewayConfig:
    mode: str
    base_url: str | None
    client_id: str | None
    client_secret: str | None
    timeout_seconds: float


_PLACEHOLDER_MARKERS = ("replace_with", "example", "changeme", "dummy")


def resolve_gsp_config() -> GSPGatewayConfig:
    mode = (os.getenv("GSP_MODE") or getattr(settings, "GSP_MODE", "sandbox") or "sandbox").strip().lower()
    if mode not in ("sandbox", "live"):
        raise GSPIntegrationUnavailableError(f"GSP_CONFIGURATION_ERROR: Unsupported GSP_MODE '{mode}'.")
    base_url = (os.getenv("GSTN_GSP_BASE_URL") or getattr(settings, "GSTN_GSP_BASE_URL", None) or "").strip() or None
    client_id = (os.getenv("GSTN_GSP_CLIENT_ID") or getattr(settings, "GSTN_GSP_CLIENT_ID", None) or "").strip() or None
    client_secret = (os.getenv("GSTN_GSP_CLIENT_SECRET") or getattr(settings, "GSTN_GSP_CLIENT_SECRET", None) or "").strip() or None
    timeout = float(os.getenv("GSTN_GSP_TIMEOUT_SECONDS") or getattr(settings, "GSTN_GSP_TIMEOUT_SECONDS", 15.0) or 15.0)
    return GSPGatewayConfig(
        mode=mode,
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
        timeout_seconds=timeout,
    )


def validate_live_gsp_credentials(cfg: GSPGatewayConfig) -> None:
    if cfg.mode != "live":
        return
    if not (cfg.base_url and cfg.client_id and cfg.client_secret):
        raise GSPIntegrationUnavailableError(
            "GSP_LIVE_INTEGRATION_UNAVAILABLE: Live GSTN filing requires authorized GSTN_GSP_BASE_URL, "
            "GSTN_GSP_CLIENT_ID, and GSTN_GSP_CLIENT_SECRET. Sandbox fallback is strictly prohibited."
        )
    lowered = f"{cfg.base_url} {cfg.client_id} {cfg.client_secret}".lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        raise GSPIntegrationUnavailableError(
            "GSP_LIVE_INTEGRATION_UNAVAILABLE: Placeholder GSP credentials detected."
        )
    if not cfg.base_url.lower().startswith("https://") or "localhost" in cfg.base_url.lower() or "127.0.0.1" in cfg.base_url:
        raise GSPIntegrationUnavailableError(
            "GSP_LIVE_INTEGRATION_UNAVAILABLE: Live GSP endpoint must use remote https:// URL."
        )


def dispatch_gstr3b_filing(
    *,
    gstin: str,
    tax_period: str,
    payload_dict: Dict[str, Any],
    checker_approved: bool,
) -> Tuple[str, str, str, str, bool]:
    """
    Dispatches GSTR-3B filing through the configured GSP boundary.
    - 'sandbox' mode runs simulate_gsp_gstn_filing() and returns is_live_portal_filing=False.
    - 'live' mode validates authorized credentials and fails closed because live
      empanelled GSP HTTP/mTLS/EVC-DSC transport requires provider onboarding.
    Returns (arn, cin, filing_date_iso, payload_hash, is_live_portal_filing).
    """
    cfg = resolve_gsp_config()
    if cfg.mode == "live":
        validate_live_gsp_credentials(cfg)
        raise GSPIntegrationUnavailableError(
            "GSP_LIVE_TRANSPORT_NOT_IMPLEMENTED: Live empanelled GSP HTTP/mTLS signing/EVC adapter "
            "is awaiting authorized GSP onboarding; refusing to simulate live GSTN portal filing."
        )

    arn, cin, filing_date_iso, payload_hash = simulate_gsp_gstn_filing(
        gstin=gstin,
        tax_period=tax_period,
        payload_dict=payload_dict,
        checker_approved=checker_approved,
    )
    return arn, cin, filing_date_iso, payload_hash, False


def check_gstr3b_filing_status(gstin: str, tax_period: str, arn: str) -> Dict[str, Any]:
    """Queries GSP gateway for return status (fails closed in live mode without provider transport)."""
    cfg = resolve_gsp_config()
    if cfg.mode == "live":
        validate_live_gsp_credentials(cfg)
        raise GSPIntegrationUnavailableError(
            "GSP_LIVE_TRANSPORT_NOT_IMPLEMENTED: Live GSP status adapter is awaiting provider onboarding."
        )
    return {
        "gstin": gstin,
        "tax_period": tax_period,
        "arn": arn,
        "status": "FILED_SANDBOX",
        "is_live_portal_filing": False,
    }


def download_gstr2b_json(gstin: str, tax_period: str) -> Dict[str, Any]:
    """Downloads GSTR-2B JSON from GSP (fails closed in live/sandbox portal fetch without onboarding)."""
    cfg = resolve_gsp_config()
    if cfg.mode == "live":
        validate_live_gsp_credentials(cfg)
        raise GSPIntegrationUnavailableError(
            "GSP_LIVE_TRANSPORT_NOT_IMPLEMENTED: Live GSP GSTR-2B pull adapter is awaiting provider onboarding."
        )
    raise GSPIntegrationUnavailableError(
        "GSP_SANDBOX_PULL_NOT_SUPPORTED: Upload official GSTR-2B JSON/Excel statement via /reconciliation/gstr2b/upload."
    )
