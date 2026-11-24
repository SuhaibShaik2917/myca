# MyCA v1.0.15 Security Verification

**Target Release:** `v1.0.15`  
**Evaluation Standard:** Independent Security Audit Invariants & Trust Boundary Enforcement  

---

## 1. Mandatory PKCE S256 & State Parameter Enforcement

### Attack Vectors Addressed
- Authorization Code Interception & Cross-Client Injection
- Cross-Site Request Forgery (CSRF) on Token Exchange
- Session Fixation & Nonce Substitution

### Implementation Details
1. **Mandatory Nonce Issuance Parameters:**
   - In `backend/app/modules/auth/api.py`, `IdpNonceRequest` enforces:
     - `state: str` with `min_length=16`, `max_length=128`.
     - `code_challenge: str` with regex validation matching standard Base64URL encoding (`^[A-Za-z0-9_-]{43,128}$`).
   - Requests omitting either parameter or submitting empty/whitespace strings are rejected with HTTP 422 Unprocessable Entity or HTTP 400 Bad Request.
2. **Mandatory Token Exchange Parameters:**
   - `IdpTokenExchangeRequest` enforces:
     - `state: str` with `min_length=16`, `max_length=128`.
     - `code_verifier: str` with `min_length=43`, `max_length=128`.
   - `consume_idp_login_nonce` in `backend/app/modules/auth/nonce_service.py` computes:
     ```python
     expected_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
     ```
   - Verifies `expected_challenge` against `nonce_record.code_challenge` using constant-time comparison `hmac.compare_digest()`. Mismatched verifiers fail with HTTP 401 `PKCE_VERIFICATION_FAILED`.
   - Verifies `state` against `nonce_record.state` using `hmac.compare_digest()`. Mismatched states fail with HTTP 401 `STATE_MISMATCH`.

---

## 2. HttpOnly-Cookie-Only Session Binding

### Attack Vectors Addressed
- XSS-Based Session Token Exfiltration
- Cross-Browser Nonce Stealing & Injection
- Header Spoofing via Cross-Origin Reverse Proxies

### Implementation Details
1. **Removal of Response Body Exposure:**
   - In `backend/app/modules/auth/api.py`, `IdpNonceResponse` excludes `session_token`.
   - The raw session token is never transmitted in response JSON, preventing JavaScript running in the browser from accessing it.
2. **HttpOnly Cookie Issuance:**
   - The session token is transmitted exclusively via `Set-Cookie: myca_auth_session=<token>; HttpOnly; SameSite=Lax; Path=/api/v1/auth; Secure`.
3. **Strict Resolution on Exchange:**
   - `exchange_idp_token` extracts the session token exclusively from `request.cookies.get("myca_auth_session")`.
   - All legacy header fallbacks (`X-Session-Token`, `X-Auth-Session-Id`) and JSON body fallbacks were removed.
   - If the cookie is absent, the exchange endpoint rejects immediately with HTTP 401 `MISSING_SESSION_COOKIE`.
   - Upon successful verification, the cookie is expired (`response.delete_cookie("myca_auth_session", path="/api/v1/auth")`).

---

## 3. Constant-Time Cryptographic Comparison

### Attack Vectors Addressed
- Timing Side-Channel Attacks on PKCE Code Challenges
- Timing Leakage on State Parameter Matching
- Timing Leakage on Session Token Binding Hashes

### Implementation Details
- Standard equality operators (`==`) were replaced with `hmac.compare_digest()` for all security-critical token verifications in `backend/app/modules/auth/nonce_service.py`:
  1. `hmac.compare_digest(expected_challenge, nonce_record.code_challenge)`
  2. `hmac.compare_digest(state, nonce_record.state)`
  3. `hmac.compare_digest(incoming_session_hash, nonce_record.session_binding_hash)`

---

## 4. Distributed Rate Limiting via PostgreSQL (`public.auth_rate_limits`)

### Attack Vectors Addressed
- Multi-Worker Distributed Denial of Service (DoS) on Nonce Issuance
- Bypassing In-Memory Rate Limiters via Worker Fanning / Container Scaling

### Implementation Details
1. **Database Schema (`0029_auth_rate_limits.py`):**
   ```sql
   CREATE TABLE public.auth_rate_limits (
       rate_key VARCHAR(128) PRIMARY KEY,
       window_start TIMESTAMPTZ NOT NULL,
       request_count INTEGER NOT NULL DEFAULT 1
   );
   ```
2. **Atomic Upsert Enforcement:**
   - In `backend/app/modules/auth/nonce_service.py`, `check_distributed_rate_limit()` executes:
     ```sql
     INSERT INTO public.auth_rate_limits (rate_key, window_start, request_count)
     VALUES (:key, :now, 1)
     ON CONFLICT (rate_key) DO UPDATE
     SET request_count = CASE
             WHEN auth_rate_limits.window_start <= :now - INTERVAL '60 seconds' THEN 1
             ELSE auth_rate_limits.request_count + 1
         END,
         window_start = CASE
             WHEN auth_rate_limits.window_start <= :now - INTERVAL '60 seconds' THEN :now
             ELSE auth_rate_limits.window_start
         END
     RETURNING request_count;
     ```
   - Enforces 20 req/min for IP keys (`ip:<ip>`) and 30 req/min for Org keys (`org:<org_id>`).
   - Row-level locking on `rate_key` guarantees atomicity across distributed application instances.

---

## 5. Atomic Active Nonce Quota under Advisory Locks

### Attack Vectors Addressed
- Concurrency Race Conditions on Active Nonce Quotas (Count-then-Insert Race)
- Storage Exhaustion via Concurrent Worker Flooding

### Implementation Details
- In `backend/app/modules/auth/nonce_service.py`, `create_idp_login_nonce` acquires a PostgreSQL transaction-scoped advisory lock before querying active nonces:
  ```sql
  SELECT pg_advisory_xact_lock(hashtext(:org_id));
  ```
- Any concurrent requests for the same organization serialize on the advisory lock. The active nonce count check (`SELECT count(*) FROM public.idp_authentication_nonces WHERE org_id = :org_id AND status = 'ACTIVE' AND expires_at > NOW()`) and subsequent insert execute inside the same transaction, guaranteeing the 5 active nonces limit is never exceeded.

---

## 6. Bounded Cleanup & 24-Hour Replay Retention

### Attack Vectors Addressed
- Token Replay Attacks during Identity Token Validity Window
- Table Growth & Unbounded Storage Degradation

### Implementation Details
- During nonce issuance, safe cleanup executes under the advisory lock:
  1. Expired active nonces (`status = 'ACTIVE' AND expires_at < NOW()`) are safely deleted.
  2. Consumed nonces older than 24 hours (`status = 'CONSUMED' AND consumed_at < NOW() - INTERVAL '24 hours'`) are pruned to keep table size bounded.
  3. Consumed nonces within 24 hours are strictly preserved to maintain replay protection against token re-submission within typical IdP token validity periods.
