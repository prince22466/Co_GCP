import argparse
import asyncio
import csv
import json
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path

import aiohttp


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    k = (len(values) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return values[int(k)]
    return values[f] * (c - k) + values[c] * (k - f)


def read_trace(path):
    with open(path, newline="") as f:
        return [dict(second=int(r["second"]), phase=r["phase"], rps=int(r["rps"])) for r in csv.DictReader(f)]


async def one_request(session, url, due, second, stats, timeout_s):
    delay = due - time.perf_counter()
    if delay > 0:
        await asyncio.sleep(delay)
    sent = time.perf_counter()
    late_ms = max(0.0, (sent - due) * 1000.0)
    ok = False
    status = 0
    latency_ms = None
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
            await resp.read()
            status = resp.status
            ok = 200 <= status < 300
    except Exception:
        pass
    finally:
        latency_ms = (time.perf_counter() - sent) * 1000.0
        bucket = stats[second]
        bucket["attempted"] += 1
        bucket["success"] += int(ok)
        bucket["error"] += int(not ok)
        bucket["latencies_ms"].append(latency_ms)
        bucket["schedule_lateness_ms"].append(late_ms)
        if status:
            bucket["statuses"][str(status)] += 1


async def run(args):
    trace = read_trace(args.trace)
    stats = defaultdict(lambda: {
        "attempted": 0,
        "success": 0,
        "error": 0,
        "latencies_ms": [],
        "schedule_lateness_ms": [],
        "statuses": defaultdict(int),
    })
    pending = set()
    connector = aiohttp.TCPConnector(limit=args.max_connections, ttl_dns_cache=300)
    start = time.perf_counter()

    async with aiohttp.ClientSession(connector=connector) as session:
        for row in trace:
            sec = row["second"]
            rps = row["rps"]
            second_start = start + sec
            # Schedule requests evenly through the second. The loop itself never waits
            # for responses, so slow servers do not lower the offered load.
            for i in range(rps):
                due = second_start + (i / rps)
                task = asyncio.create_task(one_request(session, args.target, due, sec, stats, args.timeout))
                pending.add(task)
                task.add_done_callback(pending.discard)

            # Pace creation of the next second's requests, but do not block on responses.
            next_sec = start + sec + 1
            delay = next_sec - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)

        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    wall = time.perf_counter() - start
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    all_lat = []
    all_late = []
    attempted = success = errors = 0
    with (output / "per_second.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "second", "phase", "offered_rps", "attempted", "success", "errors",
            "p50_ms", "p95_ms", "p99_ms", "mean_ms", "schedule_lateness_p95_ms"
        ])
        phase_by_second = {r["second"]: r["phase"] for r in trace}
        rps_by_second = {r["second"]: r["rps"] for r in trace}
        for sec in range(len(trace)):
            b = stats[sec]
            lat = b["latencies_ms"]
            late = b["schedule_lateness_ms"]
            attempted += b["attempted"]
            success += b["success"]
            errors += b["error"]
            all_lat.extend(lat)
            all_late.extend(late)
            w.writerow([
                sec, phase_by_second[sec], rps_by_second[sec], b["attempted"], b["success"], b["error"],
                percentile(lat, 50), percentile(lat, 95), percentile(lat, 99),
                statistics.fmean(lat) if lat else None,
                percentile(late, 95),
            ])

    summary = {
        "target": args.target,
        "trace": args.trace,
        "trace_seconds": len(trace),
        "wall_seconds": wall,
        "requests_attempted": attempted,
        "requests_successful": success,
        "requests_failed": errors,
        "success_rate": (success / attempted) if attempted else 0.0,
        "achieved_attempt_rate": attempted / wall if wall else 0.0,
        "latency_ms": {
            "p50": percentile(all_lat, 50),
            "p95": percentile(all_lat, 95),
            "p99": percentile(all_lat, 99),
            "mean": statistics.fmean(all_lat) if all_lat else None,
        },
        "scheduler_lateness_ms": {
            "p95": percentile(all_late, 95),
            "p99": percentile(all_late, 99),
        },
    }
    with (output / "summary.json").open("w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))


def main():
    p = argparse.ArgumentParser(description="Open-loop deterministic HTTP workload replayer")
    p.add_argument("--target", required=True)
    p.add_argument("--trace", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--max-connections", type=int, default=3000)
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
