from datetime import datetime, timezone

from resource_manager import cloud_tools
from resource_manager.cloud_tools import (
    CapacitySnapshot,
    RuntimeSettings,
    guarded_recommendation,
)


def settings(**overrides):
    values = {
        "project_id": "test-project",
        "region": "europe-central2",
        "mig_name": "test-mig",
        "state_bucket": "test-state",
    }
    values.update(overrides)
    return RuntimeSettings(**values)


def test_holds_while_instances_or_metrics_are_pending():
    result = guarded_recommendation(
        settings(),
        CapacitySnapshot(2, 0.90, 2, 1, True),
        last_resize=None,
        now=datetime.now(timezone.utc),
    )

    assert result["action"] == "hold_pending_instances"
    assert result["proposed_units"] == 2


def test_live_snapshot_recommends_only_one_unit():
    result = guarded_recommendation(
        settings(),
        CapacitySnapshot(2, 0.90, 2, 2, False),
        last_resize=None,
        now=datetime.now(timezone.utc),
    )

    assert result["action"] == "scale_up"
    assert result["proposed_units"] == 3


def test_holds_if_existing_capacity_is_outside_guardrails():
    result = guarded_recommendation(
        settings(),
        CapacitySnapshot(5, 0.90, 5, 5, False),
        last_resize=None,
        now=datetime.now(timezone.utc),
    )

    assert result["action"] == "hold_out_of_bounds"
    assert result["proposed_units"] == 5


def test_dry_run_never_calls_resize(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "test-project")
    monkeypatch.setenv("REGION", "europe-central2")
    monkeypatch.setenv("MIG_NAME", "test-mig")
    monkeypatch.setenv("STATE_BUCKET", "test-state")
    monkeypatch.setenv("SCALING_ENABLED", "false")
    monkeypatch.setattr(
        cloud_tools,
        "observe_capacity",
        lambda _: CapacitySnapshot(2, 0.90, 2, 2, False),
    )

    class FakeStore:
        def last_resize(self):
            return None

        def record_resize(self, **_):
            raise AssertionError("dry-run must not record a resize")

    monkeypatch.setattr(cloud_tools, "CooldownStore", lambda *_: FakeStore())
    monkeypatch.setattr(
        cloud_tools,
        "_resize",
        lambda *_: (_ for _ in ()).throw(AssertionError("dry-run must not resize")),
    )

    result = cloud_tools.manage_mig_capacity()

    assert result["mode"] == "dry_run"
    assert result["applied"] is False
    assert result["recommendation"]["action"] == "scale_up"


def test_live_mode_applies_guarded_size_and_records_state(monkeypatch):
    monkeypatch.setenv("PROJECT_ID", "test-project")
    monkeypatch.setenv("REGION", "europe-central2")
    monkeypatch.setenv("MIG_NAME", "test-mig")
    monkeypatch.setenv("STATE_BUCKET", "test-state")
    monkeypatch.setenv("SCALING_ENABLED", "true")
    monkeypatch.setattr(
        cloud_tools,
        "observe_capacity",
        lambda _: CapacitySnapshot(2, 0.90, 2, 2, False),
    )

    recorded = {}

    class FakeStore:
        def last_resize(self):
            return None

        def record_resize(self, **kwargs):
            recorded.update(kwargs)

    monkeypatch.setattr(cloud_tools, "CooldownStore", lambda *_: FakeStore())
    monkeypatch.setattr(
        cloud_tools,
        "_resize",
        lambda config, proposed, request_id: recorded.update(
            config=config, proposed=proposed, request_id=request_id
        ),
    )

    result = cloud_tools.manage_mig_capacity()

    assert result["mode"] == "live"
    assert result["applied"] is True
    assert recorded["proposed"] == 3
    assert recorded["previous_units"] == 2
    assert recorded["proposed_units"] == 3
