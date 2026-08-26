#!/usr/bin/env python3
"""
Stage-1 GKE and ADK-managed Compute Engine benchmark runner.

Responsibilities:
  * read platform endpoints and settings from their Terraform outputs
  * replay a deterministic open-loop HTTP workload
  * reset and sample either GKE/HPA or regional MIG capacity
  * save one self-contained dataset for every scenario run

Requires:
  * gcloud + kubectl + terraform in PATH
  * aiohttp (pip install -r experiment-requirements.txt)
  * current gcloud authentication with access to the selected deployments
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import random
import statistics
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp


ROOT = Path(__file__).resolve().parent
DEFAULT_GKE_TERRAFORM_DIR = ROOT / "terraform"
DEFAULT_AGENT_TERRAFORM_DIR = (
    ROOT / "resource_manager_adk" / "v1" / "deployment" / "terraform"
)
DEFAULT_SCENARIOS = ROOT / "scenarios" / "stage1.json"
DEFAULT_RESULTS = ROOT / "results"


def run_cmd(args: list[str], *, cwd: Path | None = None, check: bool = True) -> str:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"required command is not installed or not in PATH: {args[0]}"
        ) from exc
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr.strip()}"
        )
    return proc.stdout


def json_cmd(args: list[str], *, cwd: Path | None = None) -> Any:
    return json.loads(run_cmd(args, cwd=cwd))


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


def terraform_outputs(terraform_dir: Path) -> dict[str, Any]:
    raw = json_cmd(["terraform", "output", "-json"], cwd=terraform_dir)
    return {key: value["value"] for key, value in raw.items()}


def require_outputs(
    outputs: dict[str, Any], required: set[str], terraform_dir: Path
) -> None:
    missing = sorted(required - outputs.keys())
    if missing:
        raise RuntimeError(
            f"Terraform state in {terraform_dir} is missing outputs: "
            + ", ".join(missing)
            + ". Apply the current Terraform configuration before running the experiment."
        )


def configure_kubectl(outputs: dict[str, Any]) -> None:
    run_cmd(
        [
            "gcloud",
            "container",
            "clusters",
            "get-credentials",
            str(outputs["cluster_name"]),
            "--region",
            str(outputs["region"]),
            "--quiet",
        ]
    )


def kubectl_json(args: list[str]) -> dict[str, Any]:
    return json_cmd(["kubectl", *args])


def kubectl(args: list[str]) -> str:
    return run_cmd(["kubectl", *args])


def mig_manager(outputs: dict[str, Any]) -> dict[str, Any]:
    return json_cmd(
        [
            "gcloud",
            "compute",
            "instance-groups",
            "managed",
            "describe",
            str(outputs["mig_name"]),
            "--project",
            str(outputs["project_id"]),
            "--region",
            str(outputs["region"]),
            "--format=json",
        ]
    )


def mig_instances(outputs: dict[str, Any]) -> list[dict[str, Any]]:
    return json_cmd(
        [
            "gcloud",
            "compute",
            "instance-groups",
            "managed",
            "list-instances",
            str(outputs["mig_name"]),
            "--project",
            str(outputs["project_id"]),
            "--region",
            str(outputs["region"]),
            "--format=json",
        ]
    )


def parse_cpu_cores(quantity: str) -> float:
    q = quantity.strip()
    if q.endswith("n"):
        return float(q[:-1]) / 1_000_000_000
    if q.endswith("u"):
        return float(q[:-1]) / 1_000_000
    if q.endswith("m"):
        return float(q[:-1]) / 1000
    return float(q)


def parse_memory_gib(quantity: str) -> float:
    q = quantity.strip()
    binary = {"Ki": 1 / (1024 * 1024), "Mi": 1 / 1024, "Gi": 1, "Ti": 1024}
    decimal = {"K": 1e3 / (1024**3), "M": 1e6 / (1024**3), "G": 1e9 / (1024**3)}
    for suffix, factor in binary.items():
        if q.endswith(suffix):
            return float(q[: -len(suffix)]) * factor
    for suffix, factor in decimal.items():
        if q.endswith(suffix):
            return float(q[: -len(suffix)]) * factor
    return float(q) / (1024**3)


def load_scenarios(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


def expand_scenario(name: str, scenario: dict[str, Any], rps_scale: float) -> list[dict[str, Any]]:
    rng = random.Random(int(scenario.get("seed", 1)))
    noise_fraction = float(scenario.get("noise_fraction", 0.0))
    rows: list[dict[str, Any]] = []
    second = 0

    for phase in scenario["phases"]:
        duration = int(phase["duration_s"])
        start_rps = float(phase["start_rps"])
        end_rps = float(phase["end_rps"])
        for i in range(duration):
            frac = 0.0 if duration <= 1 else i / (duration - 1)
            baseline = start_rps + (end_rps - start_rps) * frac
            noise = rng.uniform(-noise_fraction, noise_fraction)
            rps = max(1, int(round(baseline * (1.0 + noise) * rps_scale)))
            rows.append(
                {
                    "second": second,
                    "scenario": name,
                    "phase": phase["name"],
                    "offered_rps": rps,
                }
            )
            second += 1
    return rows


def hpa_cpu_percent(hpa: dict[str, Any]) -> float | None:
    status = hpa.get("status", {})
    if status.get("currentCPUUtilizationPercentage") is not None:
        return float(status["currentCPUUtilizationPercentage"])

    # autoscaling/v2 shape, in case the API server returns/normalizes it later.
    for metric in status.get("currentMetrics", []) or []:
        resource = metric.get("resource") or {}
        if resource.get("name") == "cpu":
            util = (resource.get("current") or {}).get("averageUtilization")
            if util is not None:
                return float(util)
    return None


def pod_usage(namespace: str, pod_prefix: str) -> tuple[float | None, float | None]:
    try:
        metrics = kubectl_json(
            [
                "get",
                "--raw",
                f"/apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods",
            ]
        )
    except Exception:
        return None, None

    cpu = 0.0
    memory = 0.0
    matched = 0
    for item in metrics.get("items", []):
        name = item.get("metadata", {}).get("name", "")
        if not name.startswith(pod_prefix + "-"):
            continue
        matched += 1
        for container in item.get("containers", []):
            usage = container.get("usage", {})
            if "cpu" in usage:
                cpu += parse_cpu_cores(usage["cpu"])
            if "memory" in usage:
                memory += parse_memory_gib(usage["memory"])
    if not matched:
        return None, None
    return cpu, memory


async def wait_for_one_replica(
    namespace: str,
    deployment: str,
    hpa: str,
    timeout_s: int,
) -> None:
    kubectl(["-n", namespace, "scale", "deployment", deployment, "--replicas=1"])
    started = time.monotonic()
    consecutive = 0

    while time.monotonic() - started < timeout_s:
        dep = kubectl_json(["-n", namespace, "get", "deployment", deployment, "-o", "json"])
        h = kubectl_json(["-n", namespace, "get", "hpa", hpa, "-o", "json"])
        status = dep.get("status", {})
        ready = int(status.get("readyReplicas", 0) or 0)
        replicas = int(status.get("replicas", 0) or 0)
        desired = int(h.get("status", {}).get("desiredReplicas", 1) or 1)

        if ready == 1 and replicas == 1 and desired <= 1:
            consecutive += 1
            if consecutive >= 3:
                return
        else:
            consecutive = 0
        await asyncio.sleep(10)

    raise TimeoutError(
        f"deployment did not return to one stable replica within {timeout_s}s; "
        "HPA default scale-down stabilization can make resets take several minutes"
    )


def resize_mig(outputs: dict[str, Any], size: int) -> None:
    run_cmd(
        [
            "gcloud",
            "compute",
            "instance-groups",
            "managed",
            "resize",
            str(outputs["mig_name"]),
            "--project",
            str(outputs["project_id"]),
            "--region",
            str(outputs["region"]),
            "--size",
            str(size),
            "--quiet",
        ]
    )


def mig_capacity_snapshot(
    manager: dict[str, Any], instances: list[dict[str, Any]]
) -> dict[str, Any]:
    target = int(manager.get("targetSize", 0) or 0)
    allocated = len(instances)
    ready = sum(
        1
        for item in instances
        if str(item.get("instanceStatus", "")).upper() == "RUNNING"
        and str(item.get("currentAction", "NONE") or "NONE").upper()
        in {"NONE", "0"}
    )
    pending = any(
        str(item.get("currentAction", "NONE") or "NONE").upper()
        not in {"NONE", "0"}
        for item in instances
    )
    return {
        "target_units": target,
        "allocated_units": allocated,
        "ready_units": ready,
        "pending_actions": pending,
    }


async def wait_for_mig_minimum(
    outputs: dict[str, Any], timeout_s: int
) -> None:
    minimum = int(outputs["min_units"])
    await asyncio.to_thread(resize_mig, outputs, minimum)
    started = time.monotonic()
    consecutive = 0

    while time.monotonic() - started < timeout_s:
        manager, instances = await asyncio.gather(
            asyncio.to_thread(mig_manager, outputs),
            asyncio.to_thread(mig_instances, outputs),
        )
        snapshot = mig_capacity_snapshot(manager, instances)
        stable = (
            snapshot["target_units"] == minimum
            and snapshot["allocated_units"] == minimum
            and snapshot["ready_units"] == minimum
            and not snapshot["pending_actions"]
        )
        if stable:
            consecutive += 1
            if consecutive >= 3:
                return
        else:
            consecutive = 0
        await asyncio.sleep(10)

    raise TimeoutError(
        f"MIG did not return to {minimum} stable instance(s) within {timeout_s}s"
    )


async def wait_for_agent_cooldown(outputs: dict[str, Any]) -> None:
    object_uri = f"gs://{outputs['state_bucket']}/last-resize.json"
    try:
        payload = json.loads(
            await asyncio.to_thread(
                run_cmd, ["gcloud", "storage", "cat", object_uri]
            )
        )
    except (RuntimeError, json.JSONDecodeError, KeyError, ValueError):
        return

    timestamp_text = str(payload["timestamp_utc"]).replace("Z", "+00:00")
    last_resize = datetime.fromisoformat(timestamp_text)
    if last_resize.tzinfo is None:
        last_resize = last_resize.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - last_resize).total_seconds()
    remaining = max(0.0, float(outputs["cooldown_seconds"]) - elapsed)
    if remaining > 0:
        print(f"Waiting {remaining:.1f}s for the previous agent cooldown to expire...")
        await asyncio.sleep(remaining + 1.0)


async def wait_for_http_health(base_url: str, timeout_s: int) -> None:
    deadline = time.monotonic() + timeout_s
    url = f"{base_url.rstrip('/')}/healthz"
    async with aiohttp.ClientSession() as session:
        while time.monotonic() < deadline:
            try:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5.0)
                ) as response:
                    if 200 <= response.status < 300:
                        return
            except Exception:
                pass
            await asyncio.sleep(5)
    raise TimeoutError(f"workload health endpoint did not become ready: {url}")


async def one_request(
    session: aiohttp.ClientSession,
    url: str,
    due: float,
    second: int,
    stats: dict[int, dict[str, Any]],
    timeout_s: float,
) -> None:
    delay = due - time.perf_counter()
    if delay > 0:
        await asyncio.sleep(delay)

    sent = time.perf_counter()
    lateness_ms = max(0.0, (sent - due) * 1000.0)
    ok = False
    status = 0
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout_s),
        ) as response:
            await response.read()
            status = response.status
            ok = 200 <= status < 300
    except Exception:
        pass

    latency_ms = (time.perf_counter() - sent) * 1000.0
    bucket = stats[second]
    bucket["attempted"] += 1
    bucket["success"] += int(ok)
    bucket["errors"] += int(not ok)
    bucket["latencies_ms"].append(latency_ms)
    bucket["scheduler_lateness_ms"].append(lateness_ms)
    if status:
        bucket["statuses"][str(status)] += 1


async def replay_workload(
    target: str,
    trace: list[dict[str, Any]],
    timeout_s: float,
    max_connections: int,
) -> tuple[dict[int, dict[str, Any]], float]:
    stats: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "attempted": 0,
            "success": 0,
            "errors": 0,
            "latencies_ms": [],
            "scheduler_lateness_ms": [],
            "statuses": defaultdict(int),
        }
    )

    connector = aiohttp.TCPConnector(limit=max_connections, ttl_dns_cache=300)
    pending: set[asyncio.Task] = set()
    start = time.perf_counter()

    async with aiohttp.ClientSession(connector=connector) as session:
        for row in trace:
            sec = int(row["second"])
            rps = int(row["offered_rps"])
            second_start = start + sec

            for i in range(rps):
                due = second_start + i / rps
                task = asyncio.create_task(
                    one_request(session, target, due, sec, stats, timeout_s)
                )
                pending.add(task)
                task.add_done_callback(pending.discard)

            next_second = start + sec + 1
            delay = next_second - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)

        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    return stats, time.perf_counter() - start


async def collect_gke_metrics(
    namespace: str,
    deployment: str,
    hpa_name: str,
    pod_cpu: float,
    pod_memory_gib: float,
    interval_s: float,
    stop: asyncio.Event,
    experiment_start: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    last_desired: int | None = None

    while not stop.is_set():
        sample_started = time.monotonic()
        rel_s = time.perf_counter() - experiment_start

        try:
            dep, hpa = await asyncio.gather(
                asyncio.to_thread(
                    kubectl_json,
                    ["-n", namespace, "get", "deployment", deployment, "-o", "json"],
                ),
                asyncio.to_thread(
                    kubectl_json,
                    ["-n", namespace, "get", "hpa", hpa_name, "-o", "json"],
                ),
            )

            usage_cpu, usage_memory = await asyncio.to_thread(
                pod_usage, namespace, deployment
            )

            dep_status = dep.get("status", {})
            hpa_status = hpa.get("status", {})
            current = int(hpa_status.get("currentReplicas", dep_status.get("replicas", 0)) or 0)
            desired = int(hpa_status.get("desiredReplicas", current) or current)
            ready = int(dep_status.get("readyReplicas", 0) or 0)
            available = int(dep_status.get("availableReplicas", 0) or 0)

            scale_direction = ""
            if last_desired is not None:
                if desired > last_desired:
                    scale_direction = "up"
                elif desired < last_desired:
                    scale_direction = "down"
            last_desired = desired

            rows.append(
                {
                    "relative_second": rel_s,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "current_replicas": current,
                    "desired_replicas": desired,
                    "ready_replicas": ready,
                    "available_replicas": available,
                    "hpa_cpu_percent": hpa_cpu_percent(hpa),
                    "actual_cpu_cores": usage_cpu,
                    "actual_memory_gib": usage_memory,
                    "requested_cpu_cores": current * pod_cpu,
                    "requested_memory_gib": current * pod_memory_gib,
                    "scale_direction": scale_direction,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "relative_second": rel_s,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "collector_error": str(exc),
                }
            )

        elapsed = time.monotonic() - sample_started
        sleep_for = max(0.0, interval_s - elapsed)
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            pass

    return rows


async def collect_agent_metrics(
    outputs: dict[str, Any],
    interval_s: float,
    stop: asyncio.Event,
    experiment_start: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    last_target: int | None = None
    unit_vcpu = float(outputs["workload_unit_vcpu"])
    unit_memory_gib = float(outputs["workload_unit_memory_gib"])

    while not stop.is_set():
        sample_started = time.monotonic()
        rel_s = time.perf_counter() - experiment_start

        try:
            manager, instances = await asyncio.gather(
                asyncio.to_thread(mig_manager, outputs),
                asyncio.to_thread(mig_instances, outputs),
            )
            snapshot = mig_capacity_snapshot(manager, instances)
            target = int(snapshot["target_units"])
            allocated = int(snapshot["allocated_units"])

            scale_direction = ""
            if last_target is not None:
                if target > last_target:
                    scale_direction = "up"
                elif target < last_target:
                    scale_direction = "down"
            last_target = target

            rows.append(
                {
                    "relative_second": rel_s,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    **snapshot,
                    "allocated_vcpu": allocated * unit_vcpu,
                    "allocated_memory_gib": allocated * unit_memory_gib,
                    "scale_direction": scale_direction,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "relative_second": rel_s,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "collector_error": str(exc),
                }
            )

        elapsed = time.monotonic() - sample_started
        sleep_for = max(0.0, interval_s - elapsed)
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            pass

    return rows


def write_trace(path: Path, trace: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["second", "scenario", "phase", "offered_rps"]
        )
        writer.writeheader()
        writer.writerows(trace)


def write_traffic(
    path: Path,
    trace: list[dict[str, Any]],
    stats: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    all_latencies: list[float] = []
    all_lateness: list[float] = []
    attempted = success = errors = 0
    slo_bad_seconds = 0
    evaluated_seconds = 0

    fields = [
        "second",
        "phase",
        "offered_rps",
        "attempted",
        "success",
        "errors",
        "error_rate",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "mean_ms",
        "scheduler_lateness_p95_ms",
    ]

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for row in trace:
            sec = int(row["second"])
            b = stats[sec]
            lat = b["latencies_ms"]
            late = b["scheduler_lateness_ms"]
            n = int(b["attempted"])
            err = int(b["errors"])
            err_rate = err / n if n else 0.0
            p99 = percentile(lat, 99)

            attempted += n
            success += int(b["success"])
            errors += err
            all_latencies.extend(lat)
            all_lateness.extend(late)

            if n:
                evaluated_seconds += 1
                if (p99 is not None and p99 > 500.0) or err_rate > 0.01:
                    slo_bad_seconds += 1

            writer.writerow(
                {
                    "second": sec,
                    "phase": row["phase"],
                    "offered_rps": row["offered_rps"],
                    "attempted": n,
                    "success": b["success"],
                    "errors": err,
                    "error_rate": err_rate,
                    "p50_ms": percentile(lat, 50),
                    "p95_ms": percentile(lat, 95),
                    "p99_ms": p99,
                    "mean_ms": statistics.fmean(lat) if lat else None,
                    "scheduler_lateness_p95_ms": percentile(late, 95),
                }
            )

    return {
        "requests_attempted": attempted,
        "requests_successful": success,
        "requests_failed": errors,
        "success_rate": success / attempted if attempted else 0.0,
        "latency_ms": {
            "p50": percentile(all_latencies, 50),
            "p95": percentile(all_latencies, 95),
            "p99": percentile(all_latencies, 99),
            "mean": statistics.fmean(all_latencies) if all_latencies else None,
        },
        "scheduler_lateness_ms": {
            "p95": percentile(all_lateness, 95),
            "p99": percentile(all_lateness, 99),
        },
        "slo": {
            "definition": "per-second p99 <= 500ms AND error_rate <= 1%",
            "evaluated_seconds": evaluated_seconds,
            "violating_seconds": slo_bad_seconds,
            "violation_fraction": (
                slo_bad_seconds / evaluated_seconds if evaluated_seconds else 0.0
            ),
        },
    }


def write_gke_metrics(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "relative_second",
        "timestamp_utc",
        "current_replicas",
        "desired_replicas",
        "ready_replicas",
        "available_replicas",
        "hpa_cpu_percent",
        "actual_cpu_cores",
        "actual_memory_gib",
        "requested_cpu_cores",
        "requested_memory_gib",
        "scale_direction",
        "collector_error",
    ]

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def summarize_gke(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if "current_replicas" in r]
    if not valid:
        return {"samples": 0}

    scale_up = sum(1 for r in valid if r.get("scale_direction") == "up")
    scale_down = sum(1 for r in valid if r.get("scale_direction") == "down")

    first_scale_up = next(
        (r["relative_second"] for r in valid if r.get("scale_direction") == "up"),
        None,
    )

    replica_values = [float(r["current_replicas"]) for r in valid]
    cpu_values = [
        float(r["actual_cpu_cores"])
        for r in valid
        if r.get("actual_cpu_cores") is not None
    ]

    requested_vcpu_seconds = 0.0
    requested_gib_seconds = 0.0
    for left, right in zip(valid, valid[1:]):
        dt = max(0.0, float(right["relative_second"]) - float(left["relative_second"]))
        requested_vcpu_seconds += float(left["requested_cpu_cores"]) * dt
        requested_gib_seconds += float(left["requested_memory_gib"]) * dt

    return {
        "samples": len(valid),
        "mean_replicas": statistics.fmean(replica_values),
        "peak_replicas": max(replica_values),
        "scale_up_events": scale_up,
        "scale_down_events": scale_down,
        "first_scale_up_second": first_scale_up,
        "peak_actual_cpu_cores": max(cpu_values) if cpu_values else None,
        "requested_vcpu_seconds": requested_vcpu_seconds,
        "requested_gib_seconds": requested_gib_seconds,
        "requested_vcpu_hours": requested_vcpu_seconds / 3600.0,
        "requested_gib_hours": requested_gib_seconds / 3600.0,
    }


def write_agent_metrics(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "relative_second",
        "timestamp_utc",
        "target_units",
        "allocated_units",
        "ready_units",
        "pending_actions",
        "allocated_vcpu",
        "allocated_memory_gib",
        "scale_direction",
        "collector_error",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def summarize_agent(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if "target_units" in r]
    if not valid:
        return {"samples": 0}

    target_values = [float(r["target_units"]) for r in valid]
    allocated_values = [float(r["allocated_units"]) for r in valid]
    scale_up = sum(1 for r in valid if r.get("scale_direction") == "up")
    scale_down = sum(1 for r in valid if r.get("scale_direction") == "down")
    first_scale_up = next(
        (r["relative_second"] for r in valid if r.get("scale_direction") == "up"),
        None,
    )

    allocated_vcpu_seconds = 0.0
    allocated_gib_seconds = 0.0
    for left, right in zip(valid, valid[1:]):
        dt = max(0.0, float(right["relative_second"]) - float(left["relative_second"]))
        allocated_vcpu_seconds += float(left["allocated_vcpu"]) * dt
        allocated_gib_seconds += float(left["allocated_memory_gib"]) * dt

    return {
        "samples": len(valid),
        "mean_target_units": statistics.fmean(target_values),
        "peak_target_units": max(target_values),
        "mean_allocated_units": statistics.fmean(allocated_values),
        "peak_allocated_units": max(allocated_values),
        "scale_up_events": scale_up,
        "scale_down_events": scale_down,
        "first_scale_up_second": first_scale_up,
        "allocated_vcpu_seconds": allocated_vcpu_seconds,
        "allocated_gib_seconds": allocated_gib_seconds,
        "allocated_vcpu_hours": allocated_vcpu_seconds / 3600.0,
        "allocated_gib_hours": allocated_gib_seconds / 3600.0,
    }


async def run_one(
    system: str,
    scenario_name: str,
    scenario: dict[str, Any],
    run_index: int,
    outputs: dict[str, Any],
    args: argparse.Namespace,
) -> Path:
    if system == "gke":
        namespace = str(outputs["namespace"])
        deployment = str(outputs["deployment_name"])
        hpa_name = str(outputs["hpa_name"])
        base_url = str(outputs["application_url"]).rstrip("/")
        cpu_ms = int(outputs["work_cpu_ms"])
        await wait_for_one_replica(
            namespace=namespace,
            deployment=deployment,
            hpa=hpa_name,
            timeout_s=args.reset_timeout,
        )
    else:
        base_url = str(outputs["workload_url"]).rstrip("/")
        cpu_ms = int(outputs["workload_default_cpu_ms"])
        await wait_for_mig_minimum(outputs, args.reset_timeout)
        await wait_for_agent_cooldown(outputs)

    await wait_for_http_health(base_url, args.reset_timeout)
    target = f"{base_url}/work?cpu_ms={cpu_ms}"

    trace = expand_scenario(scenario_name, scenario, args.rps_scale)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = (
        args.results_dir
        / system
        / scenario_name
        / f"{run_id}_run{run_index:02d}"
    )
    out.mkdir(parents=True, exist_ok=False)

    write_trace(out / "trace.csv", trace)

    metadata = {
        "system": system,
        "agent_version": outputs.get("agent_version") if system == "agent" else None,
        "scenario": scenario_name,
        "description": scenario.get("description"),
        "run_index": run_index,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "target": target,
        "terraform_outputs": outputs,
        "rps_scale": args.rps_scale,
        "metrics_interval_seconds": args.metrics_interval,
    }
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2))

    experiment_start = time.perf_counter()
    stop = asyncio.Event()
    if system == "gke":
        collector_task = asyncio.create_task(
            collect_gke_metrics(
                namespace=namespace,
                deployment=deployment,
                hpa_name=hpa_name,
                pod_cpu=float(outputs["pod_cpu"]),
                pod_memory_gib=float(outputs["pod_memory_gib"]),
                interval_s=args.metrics_interval,
                stop=stop,
                experiment_start=experiment_start,
            )
        )
    else:
        collector_task = asyncio.create_task(
            collect_agent_metrics(
                outputs=outputs,
                interval_s=args.metrics_interval,
                stop=stop,
                experiment_start=experiment_start,
            )
        )

    try:
        stats, wall_seconds = await replay_workload(
            target=target,
            trace=trace,
            timeout_s=args.request_timeout,
            max_connections=args.max_connections,
        )

        # Capture scale decisions that occur immediately after the trace.
        if args.post_observe > 0:
            await asyncio.sleep(args.post_observe)
    finally:
        stop.set()
        metrics_rows = await collector_task

    traffic_summary = write_traffic(out / "traffic.csv", trace, stats)
    if system == "gke":
        write_gke_metrics(out / "gke_metrics.csv", metrics_rows)
        platform_summary = summarize_gke(metrics_rows)
    else:
        write_agent_metrics(out / "agent_metrics.csv", metrics_rows)
        platform_summary = summarize_agent(metrics_rows)

    summary = {
        "system": system,
        "agent_version": outputs.get("agent_version") if system == "agent" else None,
        "scenario": scenario_name,
        "run_index": run_index,
        "trace_seconds": len(trace),
        "wall_seconds": wall_seconds,
        "performance": traffic_summary,
        system: platform_summary,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"saved: {out}")
    return out


async def async_main(args: argparse.Namespace) -> None:
    scenarios = load_scenarios(args.scenarios)
    if args.scenario == "all":
        selected = list(scenarios)
    else:
        if args.scenario not in scenarios:
            raise SystemExit(
                f"unknown scenario {args.scenario!r}; choose one of: "
                + ", ".join(sorted(scenarios))
            )
        selected = [args.scenario]

    selected_systems = [args.system] if args.system != "both" else ["gke", "agent"]
    outputs_by_system: dict[str, dict[str, Any]] = {}

    if "gke" in selected_systems:
        gke_outputs = terraform_outputs(args.gke_terraform_dir)
        require_outputs(
            gke_outputs,
            {
                "cluster_name",
                "region",
                "namespace",
                "deployment_name",
                "hpa_name",
                "application_url",
                "work_cpu_ms",
                "pod_cpu",
                "pod_memory_gib",
            },
            args.gke_terraform_dir,
        )
        configure_kubectl(gke_outputs)
        outputs_by_system["gke"] = gke_outputs

    if "agent" in selected_systems:
        agent_outputs = terraform_outputs(args.agent_terraform_dir)
        require_outputs(
            agent_outputs,
            {
                "project_id",
                "agent_version",
                "region",
                "mig_name",
                "workload_url",
                "workload_default_cpu_ms",
                "workload_unit_vcpu",
                "workload_unit_memory_gib",
                "min_units",
                "max_units",
                "cooldown_seconds",
                "state_bucket",
                "scheduler_state",
                "scaling_mode",
                "native_autoscaler_mode",
            },
            args.agent_terraform_dir,
        )
        scheduler_enabled = str(agent_outputs["scheduler_state"]).upper() == "ENABLED"
        scaling_live = str(agent_outputs["scaling_mode"]).upper() == "LIVE"
        native_autoscaler_off = (
            str(agent_outputs["native_autoscaler_mode"]).upper() == "OFF"
        )
        if scaling_live and not native_autoscaler_off:
            raise SystemExit(
                "Agent live scaling requires native_autoscaler_mode=OFF. "
                "Apply the current Agent Terraform configuration first."
            )
        if not args.allow_agent_dry_run and not (scheduler_enabled and scaling_live):
            raise SystemExit(
                "Agent experiments require scheduler_state=ENABLED and "
                "scaling_mode=LIVE. Apply the live Terraform settings or pass "
                "--allow-agent-dry-run for a non-scaling control run."
            )
        outputs_by_system["agent"] = agent_outputs

    for scenario_name in selected:
        for run_index in range(1, args.runs + 1):
            run_systems = list(selected_systems)
            if args.system == "both" and run_index % 2 == 0:
                run_systems.reverse()
            for system in run_systems:
                print(
                    f"\n=== system={system} scenario={scenario_name} "
                    f"run={run_index}/{args.runs} ==="
                )
                await run_one(
                    system,
                    scenario_name,
                    scenarios[scenario_name],
                    run_index,
                    outputs_by_system[system],
                    args,
                )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay deterministic workloads against GKE, the ADK-managed MIG, or both."
    )
    parser.add_argument(
        "--system",
        choices=["gke", "agent", "both"],
        default="gke",
        help="Target platform. 'both' runs the same trace sequentially against each platform.",
    )
    parser.add_argument(
        "--scenario",
        default="daily_normal",
        help="Scenario name from scenarios/stage1.json, or 'all'.",
    )
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--rps-scale", type=float, default=1.0)
    parser.add_argument("--metrics-interval", type=float, default=5.0)
    parser.add_argument("--request-timeout", type=float, default=5.0)
    parser.add_argument("--max-connections", type=int, default=5000)
    parser.add_argument("--reset-timeout", type=int, default=420)
    parser.add_argument("--post-observe", type=int, default=30)
    parser.add_argument(
        "--gke-terraform-dir",
        "--terraform-dir",
        dest="gke_terraform_dir",
        type=Path,
        default=DEFAULT_GKE_TERRAFORM_DIR,
        help="GKE Terraform directory; --terraform-dir is retained as an alias.",
    )
    parser.add_argument(
        "--agent-terraform-dir",
        type=Path,
        default=DEFAULT_AGENT_TERRAFORM_DIR,
        help="ADK and managed-MIG Terraform directory.",
    )
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=DEFAULT_SCENARIOS,
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS,
    )
    parser.add_argument(
        "--allow-agent-dry-run",
        action="store_true",
        help="Allow an agent run when the Scheduler is paused or live scaling is disabled.",
    )
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.rps_scale <= 0:
        parser.error("--rps-scale must be greater than 0")
    if args.metrics_interval <= 0:
        parser.error("--metrics-interval must be greater than 0")
    return args


if __name__ == "__main__":
    asyncio.run(async_main(parse_args()))
