import json
import os
import time
from datetime import datetime, timedelta, timezone
from statistics import mean
from urllib.parse import urlparse

from google.cloud import compute_v1, monitoring_v3

PROJECT_ID = os.environ["PROJECT_ID"]
REGION = os.environ["REGION"]
MIG_NAME = os.environ["MIG_NAME"]
MIN_UNITS = int(os.getenv("MIG_MIN", "1"))
MAX_UNITS = int(os.getenv("MIG_MAX", "4"))
SCALE_UP_CPU = float(os.getenv("SCALE_UP_CPU", "0.65"))
SCALE_DOWN_CPU = float(os.getenv("SCALE_DOWN_CPU", "0.30"))
DECISION_INTERVAL = int(os.getenv("DECISION_INTERVAL", "60"))
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "120"))
LOOKBACK_SECONDS = int(os.getenv("CPU_LOOKBACK_SECONDS", "300"))

igm = compute_v1.RegionInstanceGroupManagersClient()
instances = compute_v1.InstancesClient()
monitoring = monitoring_v3.MetricServiceClient()


def log(event: str, **fields):
    payload = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    print(json.dumps(payload, sort_keys=True), flush=True)


def target_size() -> int:
    obj = igm.get(project=PROJECT_ID, region=REGION, instance_group_manager=MIG_NAME)
    return int(obj.target_size)


def managed_instance_ids() -> set[str]:
    ids: set[str] = set()
    managed = igm.list_managed_instances(
        project=PROJECT_ID,
        region=REGION,
        instance_group_manager=MIG_NAME,
    )
    for item in managed:
        if not item.instance:
            continue
        path = urlparse(item.instance).path.strip("/").split("/")
        try:
            zone = path[path.index("zones") + 1]
            name = path[path.index("instances") + 1]
            vm = instances.get(project=PROJECT_ID, zone=zone, instance=name)
            ids.add(str(vm.id))
        except (ValueError, IndexError, Exception) as exc:
            log("instance_lookup_error", instance=item.instance, error=str(exc))
    return ids


def average_cpu(instance_ids: set[str]) -> float | None:
    if not instance_ids:
        return None

    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=LOOKBACK_SECONDS)
    interval = monitoring_v3.TimeInterval(start_time=start, end_time=end)
    results = monitoring.list_time_series(
        request={
            "name": f"projects/{PROJECT_ID}",
            "filter": (
                'metric.type = "compute.googleapis.com/instance/cpu/utilization" '
                'AND resource.type = "gce_instance"'
            ),
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
        }
    )

    latest = []
    for series in results:
        instance_id = series.resource.labels.get("instance_id")
        if instance_id not in instance_ids or not series.points:
            continue
        # Cloud Monitoring returns points newest-first for this API.
        latest.append(float(series.points[0].value.double_value))
    return mean(latest) if latest else None


def resize(new_size: int) -> None:
    new_size = max(MIN_UNITS, min(MAX_UNITS, int(new_size)))
    request = compute_v1.ResizeRegionInstanceGroupManagerRequest(
        project=PROJECT_ID,
        region=REGION,
        instance_group_manager=MIG_NAME,
        size=new_size,
    )
    operation = igm.resize(request=request)
    log("resize_requested", new_size=new_size, operation=getattr(operation, "name", None))


def main():
    log(
        "controller_started",
        min_units=MIN_UNITS,
        max_units=MAX_UNITS,
        scale_up_cpu=SCALE_UP_CPU,
        scale_down_cpu=SCALE_DOWN_CPU,
        decision_interval=DECISION_INTERVAL,
        cooldown=COOLDOWN_SECONDS,
    )
    last_action = 0.0

    while True:
        cycle_started = time.time()
        try:
            size = target_size()
            ids = managed_instance_ids()
            cpu = average_cpu(ids)
            action = "hold"
            proposed = size

            if cpu is None:
                action = "hold_no_metric"
            elif time.time() - last_action < COOLDOWN_SECONDS:
                action = "hold_cooldown"
            elif cpu > SCALE_UP_CPU and size < MAX_UNITS:
                proposed = size + 1
                action = "scale_up"
            elif cpu < SCALE_DOWN_CPU and size > MIN_UNITS:
                proposed = size - 1
                action = "scale_down"

            log(
                "decision",
                target_size=size,
                observed_instances=len(ids),
                average_cpu=cpu,
                action=action,
                proposed_size=proposed,
            )

            if proposed != size:
                resize(proposed)
                last_action = time.time()
        except Exception as exc:
            log("controller_error", error=repr(exc))

        elapsed = time.time() - cycle_started
        time.sleep(max(1.0, DECISION_INTERVAL - elapsed))


if __name__ == "__main__":
    main()
