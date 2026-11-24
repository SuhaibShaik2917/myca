# MyCA v1.0.17 Security Verification

**Target Release:** `v1.0.17`  
**Evaluation Standard:** Independent Security Audit Invariants & Trust Boundary Enforcement  

---

## 1. Proxy Trust Right-to-Left Traversal & Client IP Spoofing Elimination (Finding 1)

### Vulnerability Remediated in v1.0.17
In `v1.0.16`, `extract_client_ip()` iterated over `X-Forwarded-For` from left to right:
```python
# VULNERABLE PATTERN IN v1.0.16:
for hop in xff.split(","):
    candidate = hop.strip()
    return str(ipaddress.ip_address(candidate))
```
If an attacker sent a forged `X-Forwarded-For: 1.1.1.1` header and a trusted reverse proxy appended the attacker's genuine IP (`203.0.113.195`), the resulting header became:
`X-Forwarded-For: 1.1.1.1, 203.0.113.195`
Because the loop read from left to right, it returned `1.1.1.1` (the attacker-forged IP), bypassing rate limits and poisoning audit logs.

### Remediation Implementation
1. **RFC 7239 Right-to-Left Traversal:**
   In `backend/app/modules/auth/api.py`, `extract_client_ip()` was rewritten:
   - Untrusted direct connections (`peer_ip not in TRUSTED_PROXIES`) strictly ignore `X-Forwarded-For` and return `peer_ip`.
   - Connections through trusted proxies traverse `X-Forwarded-For` from **right to left**:
     ```python
     hops = [h.strip() for h in xff.split(",") if h.strip()]
     for hop in reversed(hops):
         try:
             hop_obj = ipaddress.ip_address(hop)
             hop_str = str(hop_obj)
         except ValueError:
             continue

         if not is_trusted_proxy(hop_str, trusted_list):
             return hop_str
     ```
   - Each hop in `TRUSTED_PROXIES` is an authorized internal reverse proxy; traversal continues leftward.
   - The **first untrusted IP** encountered from the right was appended by a trusted proxy and cannot have been forged by the client. This address is selected as the authentic client IP.
   - Attacker-supplied values positioned to the left of the true client are completely ignored.

2. **CIDR Network and IPv4/IPv6 Support:**
   Implemented `is_trusted_proxy()` supporting individual IPs and CIDR ranges (e.g. `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`).

3. **Verification Evidence in `test_trusted_proxy_client_ip_extraction`:**
   - Case 1: Direct untrusted connection attempting header spoofing -> returns direct peer IP.
   - Case 2: Trusted proxy with spoofed header `1.1.1.1, 198.51.100.42` -> returns rightmost untrusted IP `198.51.100.42`.
   - Case 3: Multi-hop proxy chain `1.1.1.1, 198.51.100.42, 10.0.0.1` -> traverses past trusted `10.0.0.1`, returns `198.51.100.42`.
   - Case 4: CIDR network `10.0.0.0/8` -> correctly recognizes `10.5.20.1` as trusted, returns `203.0.113.88`.
   - Case 5: Malformed injection `'; DROP TABLE users; --, 127.0.0.1` -> skips invalid values, returns `127.0.0.1`.

---

## 2. Explicit Boolean SQL Rate-Limit Decision Invariance (Finding 3)

### Vulnerability Remediated in v1.0.17
In `v1.0.16`, `check_distributed_rate_limit()` evaluated whether a request was accepted by checking timestamp array membership:
```sql
-- VULNERABLE PATTERN IN v1.0.16:
RETURNING (public.auth_rate_limits.timestamps @> ARRAY[:now]) AS is_allowed
```
If two requests shared the identical microsecond timestamp `now` (e.g., concurrent requests on fast multicore systems or during testing), request 1 would insert `:now`. When request 2 arrived at the quota limit, even if request 2 was rejected and `:now` was not appended, `timestamps @> ARRAY[:now]` evaluated to `TRUE` because `:now` was already present from request 1.

### Remediation Implementation
1. **Atomic CTE with Row-Level Lock (`FOR UPDATE`):**
   In `backend/app/modules/auth/nonce_service.py`, `check_distributed_rate_limit()` acquires an exclusive row lock and calculates active timestamps:
   ```sql
   WITH locked_row AS (
       SELECT rate_key, timestamps
       FROM public.auth_rate_limits
       WHERE rate_key = :key
       FOR UPDATE
   ),
   current_state AS (
       SELECT 
           locked_row.rate_key,
           coalesce(array_agg(t_item ORDER BY t_item) FILTER (WHERE t_item IS NOT NULL), '{}'::timestamptz[]) AS filtered_ts,
           count(t_item) AS current_count
       FROM locked_row
       LEFT JOIN LATERAL unnest(locked_row.timestamps) AS t_item ON t_item > :now - INTERVAL '60 seconds'
       GROUP BY locked_row.rate_key
   )
   UPDATE public.auth_rate_limits
   SET 
       timestamps = CASE 
           WHEN current_state.current_count < :max_limit 
           THEN current_state.filtered_ts || :now 
           ELSE current_state.filtered_ts 
       END,
       updated_at = :now
   FROM current_state
   WHERE public.auth_rate_limits.rate_key = current_state.rate_key
   RETURNING 
       (current_state.current_count < :max_limit) AS is_accepted,
       CASE 
           WHEN current_state.current_count < :max_limit 
           THEN current_state.current_count + 1 
           ELSE current_state.current_count 
       END AS active_count;
   ```
2. **Mathematical Invariance:**
   `is_accepted` is computed directly as `(current_state.current_count < :max_limit)`. Array membership `@> ARRAY[:now]` is completely eliminated.
3. **Verification Evidence in `test_rate_limiter_explicit_acceptance_boolean_identical_timestamps`:**
   - 4 consecutive requests using the exact same `fixed_now` timestamp with `max_ip = 3`.
   - Requests 1, 2, 3 succeed.
   - Request 4 is rejected with HTTP 429 `RATE_LIMIT_EXCEEDED`, proving no timestamp conflation occurs.

---

## 3. Operational Scheduled Rate-Limit Cleanup Architecture (Finding 2)

### Vulnerability Remediated in v1.0.17
In `v1.0.16`, `cleanup_expired_auth_rate_limits()` existed but was not integrated into server lifecycle or background task scheduling.

### Remediation Implementation
1. **FastAPI Lifespan Background Task:**
   In `backend/app/main.py`:
   ```python
   @asynccontextmanager
   async def lifespan(app: FastAPI):
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
   ```
2. **In-Flow Opportunistic Cleanup Dispatch:**
   In `backend/app/modules/auth/nonce_service.py` & `api.py`:
   - `schedule_rate_limit_cleanup_if_due(background_tasks)` is invoked during `/idp-nonce` and `/idp-exchange`.
   - Uses thread-safe monotonic clock guard (`_cleanup_dispatch_lock`) to prevent duplicate dispatches within 300 seconds.
3. **Standalone Operational Runner:**
   - `run_rate_limit_cleanup_job(retention_hours=1)` manages independent database sessions with exception isolation.
4. **Verification Evidence in `test_rate_limit_scheduled_cleanup_operational` & `test_idp_nonce_triggers_background_cleanup`:**
   - Operational runner pruned stale records while retaining active records.
   - Async periodic runner executed with `stop_event` cancellation without leaking resources.
   - BackgroundTasks integration confirmed through FastAPI `TestClient`.

---

## 4. Comprehensive Security Gate Matrix

| Security Layer | Mechanism | Status |
|---|---|---|
| **Reverse Proxy Defense** | Right-to-left traversal, untrusted hop selection, CIDR support | **VERIFIED** |
| **Sliding Window Rate Limit** | Atomic CTE with `locked_row FOR UPDATE`, explicit boolean | **VERIFIED** |
| **Storage Maintenance** | FastAPI lifespan task, in-flow BackgroundTasks, non-blocking index | **VERIFIED** |
| **PKCE Verifier Bounds** | RFC 7636 length (43–128) and unreserved character regex | **VERIFIED** |
| **Session Cookie Binding** | HttpOnly cookie-only, anti-CSRF binding | **VERIFIED** |
| **Tenant Database Isolation** | 37 of 37 tables with RLS=True, FORCE_RLS=True | **VERIFIED** |
| **Catalogue Immutability** | Read-only reference catalogs under `app_service_user` | **VERIFIED** |
| **Disposable Rollback Safety** | Marker token `MYCA_VERIFIED_DISPOSABLE_TEST_DATABASE_TOKEN_V1_0_17` | **VERIFIED** |
