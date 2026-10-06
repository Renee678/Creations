"""scripts/load_test.py: free endpoints only, with the numbers a presentation needs."""

import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx

spec = importlib.util.spec_from_file_location("load_test", Path(__file__).resolve().parents[1] / "scripts" / "load_test.py")
load_test = sys.modules["load_test"] = importlib.util.module_from_spec(spec)
spec.loader.exec_module(load_test)


def test_load_test_hits_only_free_endpoints_and_reports_latency_errors_and_rate_limits():
    seen, n = [], {"i": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        n["i"] += 1
        i = n["i"]  # read before the sleep: other clients' requests count up meanwhile
        await asyncio.sleep(0.001)
        if i % 10 == 0:
            return httpx.Response(429)
        if i % 25 == 0:
            return httpx.Response(502)
        return httpx.Response(200, json={})

    result = asyncio.run(load_test.run("https://lookmate.test", clients=4, seconds=0.3, access_code="code",
                                       transport=httpx.MockTransport(handler)))
    assert seen and {r.url.path for r in seen} == {"/healthz", "/api/search"}, "never Claude or try-on"
    assert all(r.method == "GET" and r.headers["X-Access-Code"] == "code" for r in seen)
    assert sum(result.limited.values()) == len(seen) // 10
    assert sum(result.errors.values()) == len([i for i in range(1, len(seen) + 1) if i % 25 == 0 and i % 10])

    out = load_test.table(result, 4)
    assert out.startswith("4 clients for 0.")
    assert "| Endpoint | Requests | Req/s | p50 ms | p95 ms | p99 ms | Errors | 429 |" in out
    assert sum(line.startswith("|") for line in out.splitlines()) == 5, "header, separator, two endpoints, total"
    assert f"| **all** | {len(seen)} |" in out


def test_percentiles():
    values = [i / 1000 for i in range(1, 101)]
    assert [load_test.percentile(values, p) for p in (50, 95, 99)] == [0.05, 0.095, 0.099]
