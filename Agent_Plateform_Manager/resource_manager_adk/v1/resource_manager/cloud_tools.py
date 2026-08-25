"""Guarded GCP tools exposed to the ADK resource-manager agent."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from google.api_core.exceptions import NotFound
from google.cloud import compute_v1, monitoring_v3, storage

from .policy import recommend_capacity


@dataclass(frozen=True)
class RuntimeSettings:
    project_id: str
    region: str
    mig_name: str
    state_bucket: str
    min_units: int = 1
    max_units: int = 4
    scale_up_cpu: float = 0.65
    scale_down_cpu: float = 0.30
    cpu_lookback_seconds: int = 300
    cooldown_seconds: int = 120
    scaling_enabled: bool = False

    @classmethod
    def from_env(cls) -> RuntimeSettings:
        return cls(
            project_id=_required_env("PROJECT_ID"),
            region=_required_env("REGION"),
            mig_name=_required_env("MIG_NAME"),
            state_bucket=_required_env("STATE_BUCKET"),
            min_units=int(os.getenv("MIG_MIN", "1")),
            max_units=int(os.getenv("MIG_MAX", "4")),
            scale_up_cpu=float(os.getenv("SCALE_UP_CPU", "0.65")),
            scale_down_cpu=float(os.getenv("SCALE_DOWN_CPU", "0.30")),
            cpu_lookback_seconds=int(os.getenv("CPU_LOOKBACK_SECONDS", "300")),
            cooldown_seconds=int(os.getenv("COOLDOWN_SECONDS", "120")),
            scaling_enabled=_parse_bool(os.getenv("SCALING_ENABLED", "false")),
        )


@dataclass(frozen=True)
class CapacitySnapshot:
    current_units: int
    average_cpu_ratio: float | None
    managed_instances: int
    instances_with_metrics: int
    pending_actions: bool


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _log(event: str, **fields: Any) -> None:
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


class CooldownStore:
    """Persists the last successful resize in a private Cloud Storage object."""

    object_name = "last-resize.json"

    def __init__(self, project_id: str, bucket_name: str):
        client = storage.Client(project=project_id)
        self._blob = client.bucket(bucket_name).blob(self.object_name)

    def last_resize(self) -> datetime | None:
        try:
            payload = json.loads(self._blob.download_as_text())
        except NotFound:
            return None

        timestamp = datetime.fromisoformat(payload["timestamp_utc"])
        return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)

    def record_resize(
        self,
        timestamp: datetime,
        previous_units: int,
        proposed_units: int,
        request_id: str,
    ) -> None:
        self._blob.upload_from_string(
            json.dumps(
                {
                    "timestamp_utc": timestamp.astimezone(timezone.utc).isoformat(),
                    "previous_units": previous_units,
                    "proposed_units": proposed_units,
                    "request_id": request_id,
                },
                sort_keys=True,
            ),
            content_type="application/json",
        )


def observe_capacity(settings: RuntimeSettings) -> CapacitySnapshot:
    """Read target size, rollout state, and recent CPU for the configured MIG."""
    managers = compute_v1.RegionInstanceGroupManagersClient()

    manager = managers.get(
        project=settings.project_id,
        region=settings.region,
        instance_group_manager=settings.mig_name,
    )
    managed = list(
        managers.list_managed_instances(
            project=settings.project_id,
            region=settings.region,
            instance_group_manager=settings.mig_name,
        )
    )

    instance_ids: set[str] = set()
    pending_actions = False
    for item in managed:
        action = str(getattr(item, "current_action", "NONE") or "NONE").upper()
        if action not in {"NONE", "0"}:
            pending_actions = True
        if not item.id:
            pending_actions = True
            continue
        instance_ids.add(str(item.id))

    cpu_values = _latest_cpu_values(settings, instance_ids)
    if len(cpu_values) < int(manager.target_size):
        pending_actions = True

    return CapacitySnapshot(
        current_units=int(manager.target_size),
        average_cpu_ratio=mean(cpu_values) if cpu_values else None,
        managed_instances=len(managed),
        instances_with_metrics=len(cpu_values),
        pending_actions=pending_actions,
    )


def _latest_cpu_values(
    settings: RuntimeSettings, instance_ids: set[str]
) -> list[float]:
    if not instance_ids:
        return []

    end = datetime.now(timezone.utc)
    interval = monitoring_v3.TimeInterval(
        start_time=end - timedelta(seconds=settings.cpu_lookback_seconds),
        end_time=end,
    )
    client = monitoring_v3.MetricServiceClient()
    series = client.list_time_series(
        request={
            "name": f"projects/{settings.project_id}",
            "filter": (
                'metric.type = "compute.googleapis.com/instance/cpu/utilization" '
                'AND resource.type = "gce_instance"'
            ),
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
        }
    )

    values: list[float] = []
    for time_series in series:
        instance_id = time_series.resource.labels.get("instance_id")
        if instance_id in instance_ids and time_series.points:
            # Cloud Monitoring returns points newest-first for this API.
            values.append(float(time_series.points[0].value.double_value))
    return values


def _cooldown_active(
    last_resize: datetime | None,
    now: datetime,
    cooldown_seconds: int,
) -> bool:
    if last_resize is None:
        return False
    return now - last_resize < timedelta(seconds=cooldown_seconds)


def guarded_recommendation(
    settings: RuntimeSettings,
    snapshot: CapacitySnapshot,
    last_resize: datetime | None,
    now: datetime,
) -> dict[str, object]:
    """Create a recommendation while holding during incomplete telemetry."""
    if not settings.min_units <= snapshot.current_units <= settings.max_units:
        return {
            "action": "hold_out_of_bounds",
            "current_units": snapshot.current_units,
            "proposed_units": snapshot.current_units,
            "reasons": [
                "Current MIG capacity is outside the configured bounds; operator review is required."
            ],
        }
    if snapshot.pending_actions:
        return {
            "action": "hold_pending_instances",
            "current_units": snapshot.current_units,
            "proposed_units": snapshot.current_units,
            "reasons": [
                "The MIG has pending actions or not every target instance has CPU telemetry."
            ],
        }
    if snapshot.average_cpu_ratio is None:
        return {
            "action": "hold_no_metric",
            "current_units": snapshot.current_units,
            "proposed_units": snapshot.current_units,
            "reasons": ["No recent CPU metric is available for the MIG."],
        }

    return recommend_capacity(
        current_units=snapshot.current_units,
        average_cpu_ratio=snapshot.average_cpu_ratio,
        p99_latency_ms=None,
        error_rate=None,
        cooldown_active=_cooldown_active(
            last_resize, now, settings.cooldown_seconds
        ),
        min_units=settings.min_units,
        max_units=settings.max_units,
        scale_up_cpu=settings.scale_up_cpu,
        scale_down_cpu=settings.scale_down_cpu,
    )


def _resize(settings: RuntimeSettings, proposed_units: int, request_id: str) -> None:
    if not settings.min_units <= proposed_units <= settings.max_units:
        raise ValueError("Refusing a resize outside the configured capacity bounds")

    client = compute_v1.RegionInstanceGroupManagersClient()
    operation = client.resize(
        request=compute_v1.ResizeRegionInstanceGroupManagerRequest(
            project=settings.project_id,
            region=settings.region,
            instance_group_manager=settings.mig_name,
            size=proposed_units,
            request_id=request_id,
        )
    )
    operation.result(timeout=120)


def manage_mig_capacity() -> dict[str, object]:
    """Inspect the configured regional MIG and apply one guarded scaling step.

    This tool takes no model-controlled inputs. It reads the MIG and CPU metrics
    directly from GCP, recomputes the policy locally, restricts changes to one
    unit within configured bounds, honors a durable cooldown, and resizes only
    when SCALING_ENABLED is true.

    Returns:
        The live snapshot, guarded recommendation, and whether a resize was
        actually applied.
    """
    settings = RuntimeSettings.from_env()
    now = datetime.now(timezone.utc)
    snapshot = observe_capacity(settings)
    store = CooldownStore(settings.project_id, settings.state_bucket)
    recommendation = guarded_recommendation(
        settings=settings,
        snapshot=snapshot,
        last_resize=store.last_resize(),
        now=now,
    )

    proposed_units = int(recommendation["proposed_units"])
    applied = False
    request_id: str | None = None
    if recommendation["action"] in {"scale_up", "scale_down"}:
        if settings.scaling_enabled:
            request_id = str(uuid.uuid4())
            _resize(settings, proposed_units, request_id)
            store.record_resize(
                timestamp=now,
                previous_units=snapshot.current_units,
                proposed_units=proposed_units,
                request_id=request_id,
            )
            applied = True
        else:
            recommendation["reasons"].append(
                "Live scaling is disabled; the recommendation was not applied."
            )

    result: dict[str, object] = {
        "mode": "live" if settings.scaling_enabled else "dry_run",
        "applied": applied,
        "request_id": request_id,
        "snapshot": asdict(snapshot),
        "recommendation": recommendation,
    }
    _log("capacity_evaluated", **result)
    return result
