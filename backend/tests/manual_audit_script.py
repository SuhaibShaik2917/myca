import requests
import json
import uuid

BASE = "http://localhost:8001"
ORG_A = "00000000-0000-4000-a000-000000000001"
USER_A = "00000000-0000-4000-a000-000000000002"
REG_A = "00000000-0000-4000-a000-000000000003"

results = []

# 1. Health check
r = requests.get(f"{BASE}/health")
results.append({"test": "Health Check", "pass": r.status_code == 200, "status": r.status_code, "body": r.json()})

# 2. Token creation for Org A
r = requests.post(f"{BASE}/api/v1/auth/demo-token", json={"org_id": ORG_A, "user_id": USER_A})
token_a = r.json().get("access_token") if r.status_code == 200 else None
results.append({"test": "Demo Token Org A", "pass": r.status_code == 200 and token_a is not None, "status": r.status_code})

headers_a = {
    "Authorization": f"Bearer {token_a}",
    "X-Org-Id": ORG_A,
    "X-User-Id": USER_A
}

# 3. Token creation for non-member user (expect 403)
r = requests.post(f"{BASE}/api/v1/auth/demo-token", json={"org_id": ORG_A, "user_id": "00000000-0000-4000-a000-000000000099"})
results.append({"test": "Demo Token Non-Member Blocked (403)", "pass": r.status_code == 403, "status": r.status_code})

# 4. Fetch registrations for Org A
r = requests.get(f"{BASE}/api/v1/reconciliation/gst-registrations", headers=headers_a)
regs = r.json() if r.status_code == 200 else []
results.append({"test": "Get Registrations Org A", "pass": r.status_code == 200 and len(regs) > 0, "status": r.status_code, "count": len(regs)})

# 5. Fetch purchase documents for Org A
r = requests.get(f"{BASE}/api/v1/reconciliation/purchase/documents", headers=headers_a, params={"registration_id": REG_A})
results.append({"test": "Get Purchase Documents", "pass": r.status_code == 200, "status": r.status_code, "count": len(r.json()) if isinstance(r.json(), list) else 0})

# 6. Fetch ITC Ledger Summary
r = requests.get(f"{BASE}/api/v1/reconciliation/itc-ledger/summary", headers=headers_a)
results.append({"test": "Get ITC Ledger Summary", "pass": r.status_code == 200, "status": r.status_code, "body": r.json() if r.status_code == 200 else r.text})

# 7. Fetch Consolidated Exceptions (10 Queues)
r = requests.get(f"{BASE}/api/v1/reconciliation/exceptions", headers=headers_a, params={"registration_id": REG_A, "return_period": "2026-11"})
results.append({"test": "Get Consolidated Exceptions", "pass": r.status_code == 200, "status": r.status_code, "count": len(r.json()) if isinstance(r.json(), list) else 0})

# 8. Fetch Unified Financial Report
r = requests.get(f"{BASE}/api/v1/reconciliation/financial-reports/unified", headers=headers_a, params={"registration_id": REG_A, "return_period": "2026-11"})
results.append({"test": "Get Unified Financial Report", "pass": r.status_code == 200, "status": r.status_code})

# 9. Fetch Remediation Audit
r = requests.get(f"{BASE}/api/v1/reconciliation/remediation-audit", headers=headers_a, params={"registration_id": REG_A})
results.append({"test": "Get Remediation Audit", "pass": r.status_code == 200, "status": r.status_code, "count": len(r.json()) if isinstance(r.json(), list) else 0})

# 10. Fetch Unbooked Candidates
r = requests.get(f"{BASE}/api/v1/reconciliation/unbooked-candidates", headers=headers_a, params={"registration_id": REG_A, "return_period": "2026-11"})
results.append({"test": "Get Unbooked Candidates", "pass": r.status_code == 200, "status": r.status_code, "count": len(r.json()) if isinstance(r.json(), list) else 0})

# 11. Security: SQL Injection probe in query param
r = requests.get(f"{BASE}/api/v1/reconciliation/purchase/documents", headers=headers_a, params={"registration_id": f"{REG_A}' OR '1'='1"})
results.append({"test": "SQL Injection Probe Handled Safely", "pass": r.status_code in (400, 422, 200), "status": r.status_code, "note": "No 500 error"})

# 12. Security: Path Traversal probe
r = requests.get(f"{BASE}/api/v1/reconciliation/../../etc/passwd", headers=headers_a)
results.append({"test": "Path Traversal Blocked", "pass": r.status_code in (404, 400), "status": r.status_code})

# 13. Schema Validation: Missing payload on run
r = requests.post(f"{BASE}/api/v1/reconciliation/run", headers=headers_a, json={})
results.append({"test": "Recon Run Schema Validation (422)", "pass": r.status_code == 422, "status": r.status_code})

# 14. Multi-tenancy Isolation Test:
# Token claims Org A must override spoofed X-Org-Id header
r = requests.get(f"{BASE}/api/v1/reconciliation/gst-registrations", headers={"Authorization": f"Bearer {token_a}", "X-Org-Id": "00000000-0000-4000-b000-000000000001", "X-User-Id": USER_A})
results.append({"test": "Token claims override spoofed X-Org-Id", "pass": r.status_code == 200 and all(reg["org_id"] == ORG_A for reg in r.json()), "status": r.status_code})

print(json.dumps(results, indent=2))
