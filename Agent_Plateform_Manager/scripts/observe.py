import argparse
import csv
import json
import subprocess
import time
from pathlib import Path


def cmd_json(args):
    out = subprocess.check_output(args, text=True)
    return json.loads(out)


def observe_gke(namespace, deployment, unit_vcpu, unit_gib):
    pods = cmd_json(["kubectl", "-n", namespace, "get", "pods", "-l", "app=arm-web", "-o", "json"])
    running = 0
    ready = 0
    for item in pods.get("items", []):
        if item.get("status", {}).get("phase") == "Running":
            running += 1
        conditions = item.get("status", {}).get("conditions", [])
        if any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions):
            ready += 1
    return running, ready, running * unit_vcpu, running * unit_gib


def observe_mig(project, region, mig, unit_vcpu, unit_gib):
    items = cmd_json([
        "gcloud", "compute", "instance-groups", "managed", "list-instances", mig,
        "--project", project, "--region", region, "--format=json"
    ])
    allocated = len(items)
    ready = sum(1 for x in items if str(x.get("instanceStatus", "")).upper() == "RUNNING")
    return allocated, ready, allocated * unit_vcpu, allocated * unit_gib


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--system", choices=["gke", "mig"], required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--project")
    p.add_argument("--region")
    p.add_argument("--mig")
    p.add_argument("--namespace", default="arm-stage1")
    p.add_argument("--deployment", default="arm-web")
    p.add_argument("--unit-vcpu", type=float, default=2.0)
    p.add_argument("--unit-gib", type=float, default=2.0)
    p.add_argument("--interval", type=float, default=5.0)
    args = p.parse_args()

    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    with path.open("w", newline="", buffering=1) as f:
        w = csv.writer(f)
        w.writerow(["epoch_s", "elapsed_s", "units", "ready_units", "allocated_vcpu", "allocated_gib"])
        while True:
            try:
                if args.system == "gke":
                    units, ready, vcpu, gib = observe_gke(args.namespace, args.deployment, args.unit_vcpu, args.unit_gib)
                else:
                    units, ready, vcpu, gib = observe_mig(args.project, args.region, args.mig, args.unit_vcpu, args.unit_gib)
                now = time.time()
                w.writerow([now, now - start, units, ready, vcpu, gib])
            except Exception as exc:
                print(f"observer warning: {exc}", flush=True)
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
