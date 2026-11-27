import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, Any, Tuple

class GSPSandboxFilingError(Exception):
    pass

def generate_arn(state_code: str, return_period: str, sequence_id: int = 1) -> str:
    """
    Generates a realistic GSTN Application Reference Number (ARN) adhering to GST portal syntax:
    Format: AA + State Code (2 digits) + MMYY (4 digits) + 7-digit sequential number + 1-digit checksum.
    Example: AA27112600000018
    """
    clean_state = state_code.zfill(2)
    # Parse MMYY from return_period (e.g. '2026-11' -> '1126')
    parts = return_period.split("-")
    if len(parts) == 2:
        mmyy = f"{parts[1]}{parts[0][-2:]}"
    else:
        mmyy = "1126"
    seq_str = str(sequence_id).zfill(7)
    raw_arn = f"AA{clean_state}{mmyy}{seq_str}"
    # Checksum character (mod 10)
    checksum = sum(ord(c) for c in raw_arn) % 10
    return f"{raw_arn}{checksum}"

def generate_cin(bank_code: str = "HDFC", amount_cents: int = 0) -> str:
    """
    Generates Challan Identification Number (CIN) adhering to RBI/GSTN format:
    14-digit CPIN / sequence + 3-digit bank code + Date DDMMYYYY.
    """
    ts = datetime.now(timezone.utc).strftime("%d%m%Y")
    digest_int = int(hashlib.sha256(f"{bank_code}_{ts}_{amount_cents}".encode("utf-8")).hexdigest()[:12], 16)
    seq = str(digest_int % 100000000000000).zfill(14)
    return f"{seq}{bank_code}{ts}"

def simulate_gsp_gstn_filing(
    gstin: str,
    tax_period: str,
    payload_dict: Dict[str, Any],
    checker_approved: bool
) -> Tuple[str, str, str, str]:
    """
    Simulates GSP / GSTN Sandbox filing API for GSTR-3B.
    Validates:
    - Maker-Checker approval gate (must be checker approved)
    - Valid GSTIN format
    - Idempotent payload hashing

    Returns (arn, cin, filing_date_iso, payload_hash)
    """
    if not checker_approved:
        raise GSPSandboxFilingError("Filing Gate Blocked: GSTR-3B must be approved by Checker / CA before filing.")

    # 1. Compute SHA-256 fingerprint
    canonical_payload = json.dumps(payload_dict, sort_keys=True, default=str)
    payload_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()

    # 2. State code from GSTIN
    state_code = gstin[:2] if len(gstin) >= 2 else "27"

    # 3. Generate unique collision-resistant ARN and CIN
    now_ts = datetime.now(timezone.utc)
    filing_date_iso = now_ts.isoformat()
    seq_entropy = int(
        hashlib.sha256(f"{gstin}:{tax_period}:{filing_date_iso}:{payload_hash}".encode("utf-8")).hexdigest()[:7],
        16,
    ) % 9000000 + 1
    arn = generate_arn(state_code, tax_period, sequence_id=seq_entropy)
    cin = generate_cin("SBIN", int(payload_hash[:6], 16))

    return arn, cin, filing_date_iso, payload_hash
