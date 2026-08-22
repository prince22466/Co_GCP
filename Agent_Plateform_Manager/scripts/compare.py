import argparse
import csv
import json
import statistics
from pathlib import Path


def load_json(path):
    with open(path) as f:
        return json.load(f)


def integrate_resources(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {"unit_hours": 0.0, "vcpu_hours": 0.0, "gib_hours": 0.0, "peak_units": 0}
    times = [float(r["epoch_s"]) for r in rows]
    dts = [b - a for a, b in zip(times, times[1:]) if b > a]
    fallback = statistics.median(dts) if dts else 5.0
    unit_s = vcpu_s = gib_s = total_time_s = 0.0
    peak = 0
    for i, r in enumerate(rows):
        dt = (times[i + 1] - times[i]) if i + 1 < len(rows) else fallback
        units = float(r["units"])
        total_time_s += dt
        unit_s += units * dt
        vcpu_s += float(r["allocated_vcpu"]) * dt
        gib_s += float(r["allocated_gib"]) * dt
        peak = max(peak, units)
    changes = sum(1 for a, b in zip(rows, rows[1:]) if a["units"] != b["units"])
    duration_h = total_time_s / 3600
    return {
        "unit_hours": unit_s / 3600,
        "vcpu_hours": vcpu_s / 3600,
        "gib_hours": gib_s / 3600,
        "peak_units": peak,
        "mean_units": (unit_s / 3600) / duration_h if duration_h > 0 else 0.0,
        "observed_scale_changes": changes,
    }


def optional_sum(parts):
    return None if any(v is None for v in parts) else sum(parts)


def estimate_cost(system, usage, duration_h, pricing):
    lb = pricing.get("load_balancer_hour")
    if system == "gke":
        cpu = pricing.get("gke_autopilot_vcpu_hour")
        mem = pricing.get("gke_autopilot_gib_hour")
        cluster = pricing.get("gke_cluster_hour")
        return optional_sum([
            None if cpu is None else usage["vcpu_hours"] * cpu,
            None if mem is None else usage["gib_hours"] * mem,
            None if cluster is None else duration_h * cluster,
            None if lb is None else duration_h * lb,
        ])
    vm = pricing.get("mig_e2_highcpu_2_vm_hour")
    ctl = pricing.get("agent_controller_vm_hour")
    return optional_sum([
        None if vm is None else usage["unit_hours"] * vm,
        None if ctl is None else duration_h * ctl,
        None if lb is None else duration_h * lb,
    ])


def fmt(v, digits=3):
    return "N/A" if v is None else f"{v:.{digits}f}"


def slo_stats(path, p99_ms, max_error_rate):
    total = violating = 0
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            attempted = int(r["attempted"])
            if attempted <= 0:
                continue
            total += 1
            p99 = float(r["p99_ms"]) if r["p99_ms"] else 0.0
            error_rate = int(r["errors"]) / attempted
            if p99 > p99_ms or error_rate > max_error_rate:
                violating += 1
    return {
        "seconds_evaluated": total,
        "seconds_violating": violating,
        "violation_fraction": violating / total if total else 0.0,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--gke", required=True, help="GKE result directory")
    p.add_argument("--agent", required=True, help="Agent result directory")
    p.add_argument("--pricing", default=None)
    p.add_argument("--slo-p99-ms", type=float, default=500.0)
    p.add_argument("--slo-max-error-rate", type=float, default=0.01)
    args = p.parse_args()

    pricing = load_json(args.pricing) if args.pricing else {}
    result = {}
    for system, directory in [("gke", args.gke), ("agent", args.agent)]:
        directory = Path(directory)
        summary = load_json(directory / "summary.json")
        usage = integrate_resources(directory / "resources.csv")
        duration_h = summary["wall_seconds"] / 3600
        cost = estimate_cost(system, usage, duration_h, pricing)
        slo = slo_stats(directory / "per_second.csv", args.slo_p99_ms, args.slo_max_error_rate)
        million_success = summary["requests_successful"] / 1_000_000
        result[system] = {
            "summary": summary,
            "usage": usage,
            "estimated_cost": cost,
            "cost_per_million_successful": None if cost is None or million_success == 0 else cost / million_success,
            "slo": slo,
            "p99_slo_met": summary["latency_ms"]["p99"] <= args.slo_p99_ms,
        }

    print("\nStage-1 comparison\n")
    print("| Metric | GKE Autopilot | Agent + MIG |")
    print("|---|---:|---:|")
    for label, fn in [
        ("Requests attempted", lambda x: x["summary"]["requests_attempted"]),
        ("Success rate", lambda x: x["summary"]["success_rate"] * 100),
        ("p50 latency ms", lambda x: x["summary"]["latency_ms"]["p50"]),
        ("p95 latency ms", lambda x: x["summary"]["latency_ms"]["p95"]),
        ("p99 latency ms", lambda x: x["summary"]["latency_ms"]["p99"]),
        ("Peak resource units", lambda x: x["usage"]["peak_units"]),
        ("Mean resource units", lambda x: x["usage"]["mean_units"]),
        ("Observed scale changes", lambda x: x["usage"]["observed_scale_changes"]),
        ("SLO violation % of seconds", lambda x: x["slo"]["violation_fraction"] * 100),
        ("vCPU-hours", lambda x: x["usage"]["vcpu_hours"]),
        ("GiB-hours", lambda x: x["usage"]["gib_hours"]),
        ("Estimated cost", lambda x: x["estimated_cost"]),
        ("Cost / 1M successes", lambda x: x["cost_per_million_successful"]),
    ]:
        a, b = fn(result["gke"]), fn(result["agent"])
        if label in ("Success rate", "SLO violation % of seconds"):
            print(f"| {label} | {fmt(a, 2)}% | {fmt(b, 2)}% |")
        else:
            print(f"| {label} | {fmt(a)} | {fmt(b)} |")
    print(f"| p99 <= {args.slo_p99_ms:.0f} ms | {result['gke']['p99_slo_met']} | {result['agent']['p99_slo_met']} |")

    out = {"slo_p99_ms": args.slo_p99_ms, "slo_max_error_rate": args.slo_max_error_rate, **result}
    Path("results/comparison.json").write_text(json.dumps(out, indent=2))
    print("\nMachine-readable output: results/comparison.json")
    if not args.pricing:
        print("Dollar cost is N/A until you copy pricing.example.json to pricing.json, fill current rates, and pass --pricing pricing.json.")


if __name__ == "__main__":
    main()
