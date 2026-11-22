import asyncio
import time
import httpx
import statistics

BASE_URL = "http://localhost:8001"
ORG_ID = "00000000-0000-4000-a000-000000000001"
USER_ID = "00000000-0000-4000-a000-000000000002"

async def benchmark_endpoint(name: str, method: str, url: str, headers: dict = None, json_body: dict = None, concurrency: int = 20, count: int = 50):
    async with httpx.AsyncClient(timeout=10.0) as client:
        latencies = []
        errors = 0

        async def send_req():
            nonlocal errors
            start = time.perf_counter()
            try:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    resp = await client.post(url, headers=headers, json=json_body)
                elapsed = (time.perf_counter() - start) * 1000
                if resp.status_code >= 400:
                    errors += 1
                else:
                    latencies.append(elapsed)
            except Exception as e:
                errors += 1

        sem = asyncio.Semaphore(concurrency)
        async def bounded_req():
            async with sem:
                await send_req()

        tasks = [bounded_req() for _ in range(count)]
        start_total = time.perf_counter()
        await asyncio.gather(*tasks)
        total_time = (time.perf_counter() - start_total)

        if latencies:
            latencies.sort()
            p50 = statistics.median(latencies)
            p95 = latencies[int(len(latencies) * 0.95)] if len(latencies) > 1 else latencies[0]
            avg = statistics.mean(latencies)
            rps = count / total_time
            print(f"[{name}] Total: {count} | Errors: {errors} | RPS: {rps:.1f} | Min: {min(latencies):.2f}ms | Avg: {avg:.2f}ms | P50: {p50:.2f}ms | P95: {p95:.2f}ms | Max: {max(latencies):.2f}ms")
            return {
                "name": name,
                "count": count,
                "errors": errors,
                "rps": rps,
                "min_ms": min(latencies),
                "avg_ms": avg,
                "p50_ms": p50,
                "p95_ms": p95,
                "max_ms": max(latencies)
            }
        else:
            print(f"[{name}] ALL {count} REQUESTS FAILED!")
            return {"name": name, "count": count, "errors": errors, "rps": 0}

async def main():
    print("=== MyCA Performance & Concurrency Benchmark ===")
    # 1. Health check
    await benchmark_endpoint("Health Check", "GET", f"{BASE_URL}/health", count=100, concurrency=25)

    # 2. Demo Token Generation
    token_bench = await benchmark_endpoint(
        "Demo Token (Auth)", "POST", f"{BASE_URL}/api/v1/auth/demo-token",
        json_body={"user_id": USER_ID, "org_id": ORG_ID},
        count=50, concurrency=10
    )

    # Get a token for authenticated tests
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{BASE_URL}/api/v1/auth/demo-token", json={"user_id": USER_ID, "org_id": ORG_ID})
        token = r.json().get("access_token")

    auth_headers = {
        "Authorization": f"Bearer {token}",
        "X-Org-Id": ORG_ID,
        "X-User-Id": USER_ID
    }

    # 3. GST Registrations List
    await benchmark_endpoint(
        "GST Registrations (DB RLS)", "GET", f"{BASE_URL}/api/v1/reconciliation/gst-registrations",
        headers=auth_headers, count=50, concurrency=10
    )

    # 4. ITC Ledger Summary (Aggregations)
    await benchmark_endpoint(
        "ITC Ledger Summary (Aggregations)", "GET", f"{BASE_URL}/api/v1/reconciliation/itc-ledger/summary",
        headers=auth_headers, count=50, concurrency=10
    )

if __name__ == "__main__":
    asyncio.run(main())
