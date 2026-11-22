import httpx

client = httpx.Client(base_url="http://127.0.0.1:8001")
res_health = client.get("/health")
print("Health:", res_health.json())

headers = {
    "X-Org-Id": "00000000-0000-4000-a000-000000000001",
    "X-User-Id": "00000000-0000-4000-a000-000000000002"
}

res_schemes = client.get("/api/v1/scheme-navigator/schemes", headers=headers)
print("Schemes count:", len(res_schemes.json()))

res_summary = client.get("/api/v1/credit-builder/summary", headers=headers)
print("Credit Summary status:", res_summary.status_code)

res_insights = client.get("/api/v1/ai/quick-insights", headers=headers)
print("AI Quick Insights count:", len(res_insights.json()))

chat_res = client.post("/api/v1/ai/chat", json={
    "messages": [{"role": "user", "content": "Explain Rule 37 180 day rule"}]
}, headers=headers)
print("AI Chat status:", chat_res.status_code)
print("AI Chat model used:", chat_res.json().get("model_used"))
