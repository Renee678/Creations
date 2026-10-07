"""A small load test against free endpoints only: /healthz and the catalog search (embeddings, no Claude, no try-on).

    python scripts/load_test.py --url https://5-161-202-29.sslip.io --clients 20 --seconds 30

N clients each send requests back to back for T seconds, alternating health checks and searches. It prints a
Markdown table: requests/s, p50/p95/p99 latency, errors, and how many were rate-limited (429) by the gateway,
which allows 20 requests/s per IP with a burst of 40, so one machine measures the limiter beyond that.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import time
from dataclasses import dataclass, field

import httpx

QUERIES = ["black blazer", "white linen shirt", "wide leg jeans", "slip dress", "trench coat", "loafers",
           "cream knit sweater", "pleated midi skirt", "leather ankle boots", "tote bag"]


def paths():
    """Endless request paths: a health check, then a search, round the query list."""
    i = 0
    while True:
        yield "/healthz"
        yield f"/api/search?q={QUERIES[i % len(QUERIES)].replace(' ', '+')}&k=10"
        i += 1


@dataclass
class Result:
    latencies: dict[str, list[float]] = field(default_factory=dict)  # endpoint -> seconds, successful only
    errors: dict[str, int] = field(default_factory=dict)
    limited: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0

    def add(self, endpoint: str, status: int | None, latency: float) -> None:
        self.latencies.setdefault(endpoint, [])
        self.errors.setdefault(endpoint, 0)
        self.limited.setdefault(endpoint, 0)
        if status == 429:
            self.limited[endpoint] += 1
        elif status is None or status >= 400:
            self.errors[endpoint] += 1
        else:
            self.latencies[endpoint].append(latency)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))]  # nearest rank


async def run(url: str, clients: int, seconds: float, access_code: str = "",
              transport: httpx.AsyncBaseTransport | None = None) -> Result:
    result = Result()
    headers = {"X-Access-Code": access_code} if access_code else {}
    limits = httpx.Limits(max_connections=clients, max_keepalive_connections=clients)
    async with httpx.AsyncClient(base_url=url.rstrip("/"), headers=headers, timeout=30, limits=limits,
                                 transport=transport) as http:
        start = time.perf_counter()
        deadline = start + seconds

        async def client(n: int) -> None:
            gen = paths()
            for _ in range(n % 2):  # half the clients start with a search
                next(gen)
            while time.perf_counter() < deadline:
                path = next(gen)
                endpoint = path.split("?")[0]
                t = time.perf_counter()
                try:
                    status = (await http.get(path)).status_code
                except httpx.HTTPError:
                    status = None
                result.add(endpoint, status, time.perf_counter() - t)

        await asyncio.gather(*(client(n) for n in range(clients)))
        result.seconds = time.perf_counter() - start
    return result


def table(result: Result, clients: int) -> str:
    rows = ["| Endpoint | Requests | Req/s | p50 ms | p95 ms | p99 ms | Errors | 429 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|"]
    everything: list[float] = []
    totals = [0, 0, 0]
    for endpoint in sorted(result.latencies):
        ok, errors, limited = result.latencies[endpoint], result.errors[endpoint], result.limited[endpoint]
        n = len(ok) + errors + limited
        everything += ok
        totals = [totals[0] + n, totals[1] + errors, totals[2] + limited]
        rows.append(_row(endpoint, n, ok, errors, limited, result.seconds))
    rows.append(_row("**all**", totals[0], everything, totals[1], totals[2], result.seconds))
    return f"{clients} clients for {result.seconds:.1f} s\n\n" + "\n".join(rows)


def _row(name: str, n: int, ok: list[float], errors: int, limited: int, seconds: float) -> str:
    ms = [f"{percentile(ok, p) * 1000:.0f}" if ok else "-" for p in (50, 95, 99)]
    return f"| {name} | {n} | {n / seconds:.1f} | {' | '.join(ms)} | {errors} | {limited} |"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=os.environ.get("LOOKMATE_URL", "http://localhost:8000"))
    ap.add_argument("--clients", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--access-code", default=os.environ.get("LOOKMATE_ACCESS_CODE", ""))
    args = ap.parse_args()
    result = asyncio.run(run(args.url, args.clients, args.seconds, args.access_code))
    print(table(result, args.clients))


if __name__ == "__main__":
    main()
