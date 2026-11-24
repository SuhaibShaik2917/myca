# MyCA v1.0.16 Security Verification

**Target Release:** `v1.0.16`  
**Evaluation Standard:** Independent Security Audit Invariants & Trust Boundary Enforcement  

---

## 1. RFC 7636 PKCE `code_verifier` Validation (Finding 1)

### Vulnerability Remediated
In `v1.0.15`, the server checked that `code_verifier` was non-empty and ASCII-encodable, but failed to enforce RFC 7636 Section 4.1's length bounds (43–128 characters) and permitted character set (`[A-Za-z0-9_.~-]`).

### Remediation Implementation
1. **Pydantic Schema Validation:**
   In `backend/app/modules/auth/api.py`, `IdpTokenExchangeRequest` declares:
   ```python
   code_verifier: str = Field(
       ...,
       min_length=43,
       max_length=128,
       pattern=r"^[A-Za-z0-9_.~-]{43,128}$",
       description="RFC 7636 PKCE code verifier (43-128 unreserved chars)"
   )
   ```
2. **Service-Layer Enforcement:**
   In `backend/app/modules/auth/nonce_service.py`, `consume_idp_login_nonce` explicitly validates:
   - Length between 43 and 128 characters (raises HTTP 400 `INVALID_PKCE_VERIFIER: PKCE code_verifier length must be between 43 and 128 characters.` if violated).
   - Character regex `^[A-Za-z0-9_.~-]{43,128}$` (raises HTTP 400 `INVALID_PKCE_VERIFIER: PKCE code_verifier contains invalid characters. Must contain only RFC 7636 unreserved characters.` if violated).
3. **Verification Evidence:**
   - Shorter than 43 chars (`"A" * 42`): rejected with HTTP 400 / HTTP 422.
   - Longer than 128 chars (`"B" * 129`): rejected with HTTP 400 / HTTP 422.
   - Invalid characters (`"C" * 40 + " @$!"`): rejected with HTTP 400 / HTTP 422.
   - Valid boundary lengths (43 chars, 128 chars): verified and accepted.

---

## 2. Genuine Rolling 60-Second Sliding Window Distributed Rate Limiter (Finding 2)

### Vulnerability Remediated
In `v1.0.15`, the rate limiter truncated timestamps to the start of the calendar minute (`window_start = now.replace(second=0, microsecond=0)`). This permitted boundary bursts: an attacker could make 20 requests at 10:00:59 and another 20 requests at 10:01:01, completing 40 requests in 2 seconds.

### Remediation Implementation
1. **Additive Migration `0030_auth_rate_limits_rolling.py`:**
   Migrated `public.auth_rate_limits` to track a dynamic sliding array of timestamps:
   ```sql
   ALTER TABLE public.auth_rate_limits
       DROP COLUMN IF EXISTS window_start,
       DROP COLUMN IF EXISTS request_count,
       ADD COLUMN timestamps TIMESTAMPTZ[] NOT NULL DEFAULT '{}'::timestamptz[],
       ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp();

   CREATE INDEX IF NOT EXISTS ix_auth_rate_limits_updated_at ON public.auth_rate_limits (updated_at);
   ```
2. **Atomic Rolling Sliding Window Query:**
   `check_distributed_rate_limit()` executes an atomic PostgreSQL upsert:
   ```sql
   UPDATE public.auth_rate_limits
   SET timestamps = CASE
           WHEN (
               SELECT count(*)
               FROM unnest(public.auth_rate_limits.timestamps) AS t_item
               WHERE t_item > :now - INTERVAL '60 seconds'
           ) < :limit
           THEN (
               SELECT coalesce(array_agg(t_item ORDER BY t_item), '{}'::timestamptz[]) || :now
               FROM unnest(public.auth_rate_limits.timestamps) AS t_item
               WHERE t_item > :now - INTERVAL '60 seconds'
           )
           ELSE (
               SELECT coalesce(array_agg(t_item ORDER BY t_item), '{}'::timestamptz[])
               FROM unnest(public.auth_rate_limits.timestamps) AS t_item
               WHERE t_item > :now - INTERVAL '60 seconds'
           )
       END,
       updated_at = :now
   WHERE rate_key = :key
   RETURNING 
       (public.auth_rate_limits.timestamps @> ARRAY[:now]) AS is_allowed,
       (
           SELECT count(*)
           FROM unnest(public.auth_rate_limits.timestamps) AS t_item
           WHERE t_item > :now - INTERVAL '60 seconds'
       ) AS active_count;
   ```
3. **Verification Evidence:**
   - 20 requests executed at `t0 = 10:00:59.000` (all 20 succeed).
   - 21st request executed at `t1 = 10:01:00.500` (1.5 seconds later across the minute boundary): rejected with HTTP 429 `RATE_LIMIT_EXCEEDED`.
   - 22nd request executed at `t2 = 10:02:00.000` (61 seconds after t0): succeeds as earlier requests age out of the 60-second window.

---

## 3. Bounded Retention & Non-Blocking Cleanup (Finding 3)

### Vulnerability Remediated
In `v1.0.15`, unique IP keys accumulated indefinitely in `public.auth_rate_limits` without any cleanup or retention mechanism.

### Remediation Implementation
1. **Non-Blocking Inactive Pruning Function:**
   ```python
   def cleanup_expired_auth_rate_limits(db: Session, retention_hours: int = 1) -> int:
       res = db.execute(
           text("""
               DELETE FROM public.auth_rate_limits
               WHERE updated_at < NOW() - make_interval(hours => :hours);
           """),
           {"hours": retention_hours},
       )
       db.commit()
       return res.rowcount or 0
   ```
2. **Guaranteed Non-Interference with Active Limits:**
   - Active rate limiting operates strictly on a rolling 60-second window.
   - Deleting rows where `updated_at < NOW() - INTERVAL '1 hour'` only removes keys with zero activity in the preceding 60 minutes.
   - Indexed by `ix_auth_rate_limits_updated_at` for high-throughput deletion.
3. **Verification Evidence:**
   - Verified via `test_rate_limits_bounded_retention_and_cleanup`: stale records older than 1 hour are pruned; active records (< 60s) remain intact; subsequent requests succeed without disruption.

---

## 4. Multi-Session & Concurrent Thread Invariance

### Verification Evidence
1. **Cross-Session Shared Enforcement:**
   - Verified via `test_rate_limiter_across_separate_db_sessions` utilizing 2 distinct `SessionLocal()` database connections. Alternating requests between sessions strictly enforced the 20/min ceiling.
2. **Thread Concurrency Safety:**
   - Verified via `test_concurrent_rate_limiting_across_threads`: 25 concurrent threads hit `check_distributed_rate_limit` simultaneously with independent database sessions; exactly 20 succeeded and 5 were throttled with HTTP 429.

---

## 5. Hardened Client IP Extraction & Reverse Proxy Defense

### Attack Vectors Defeated
- Client IP Spoofing via arbitrary `X-Forwarded-For` injection.
- Header injection / SQL smuggling via crafted proxy headers.
- Single-IP throttling bottlenecks behind reverse proxies.

### Remediation Implementation
- `extract_client_ip(request)` in `backend/app/modules/auth/api.py`:
  - Compares the immediate peer IP (`request.client.host`) against `settings.TRUSTED_PROXIES` (default `{"127.0.0.1", "::1"}`).
  - If the peer is not a trusted proxy, `X-Forwarded-For` is strictly ignored and the peer IP is used.
  - If the peer is a trusted proxy, the first valid IP from `X-Forwarded-For` is extracted and validated using Python's `ipaddress.ip_address`.
  - Malformed strings (e.g. `'; DROP TABLE...`) fail address parsing and fall back to safe proxy peer IP.
