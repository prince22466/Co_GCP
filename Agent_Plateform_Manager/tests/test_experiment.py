import asyncio

import pytest

import experiment


def test_expand_scenario_is_deterministic_and_scaled():
    scenario = {
        "seed": 7,
        "noise_fraction": 0.0,
        "phases": [
            {
                "name": "steady",
                "duration_s": 2,
                "start_rps": 100,
                "end_rps": 100,
            }
        ],
    }

    first = experiment.expand_scenario("test", scenario, 0.2)
    second = experiment.expand_scenario("test", scenario, 0.2)

    assert first == second
    assert [row["offered_rps"] for row in first] == [20, 20]


def test_mig_capacity_snapshot_reports_rollout_state():
    snapshot = experiment.mig_capacity_snapshot(
        {"targetSize": 2},
        [
            {"instanceStatus": "RUNNING", "currentAction": "NONE"},
            {"instanceStatus": "PROVISIONING", "currentAction": "CREATING"},
        ],
    )

    assert snapshot == {
        "target_units": 2,
        "allocated_units": 2,
        "ready_units": 1,
        "pending_actions": True,
    }


def test_summarize_agent_integrates_allocated_capacity():
    rows = [
        {
            "relative_second": 0.0,
            "target_units": 1,
            "allocated_units": 1,
            "allocated_vcpu": 2.0,
            "allocated_memory_gib": 2.0,
            "scale_direction": "",
        },
        {
            "relative_second": 10.0,
            "target_units": 2,
            "allocated_units": 2,
            "allocated_vcpu": 4.0,
            "allocated_memory_gib": 4.0,
            "scale_direction": "up",
        },
        {
            "relative_second": 20.0,
            "target_units": 2,
            "allocated_units": 2,
            "allocated_vcpu": 4.0,
            "allocated_memory_gib": 4.0,
            "scale_direction": "",
        },
    ]

    summary = experiment.summarize_agent(rows)

    assert summary["scale_up_events"] == 1
    assert summary["peak_target_units"] == 2
    assert summary["allocated_vcpu_seconds"] == 60
    assert summary["allocated_gib_seconds"] == 60


def test_both_system_runs_alternate_order(monkeypatch, tmp_path):
    gke_outputs = {
        "cluster_name": "cluster",
        "region": "region",
        "namespace": "namespace",
        "deployment_name": "deployment",
        "hpa_name": "hpa",
        "application_url": "http://gke",
        "work_cpu_ms": 8,
        "pod_cpu": 2,
        "pod_memory_gib": 2,
    }
    agent_outputs = {
        "project_id": "project",
        "agent_version": "v1",
        "region": "region",
        "mig_name": "mig",
        "workload_url": "http://agent",
        "workload_default_cpu_ms": 8,
        "workload_unit_vcpu": 2,
        "workload_unit_memory_gib": 2,
        "min_units": 1,
        "max_units": 4,
        "cooldown_seconds": 120,
        "state_bucket": "state",
        "scheduler_state": "ENABLED",
        "scaling_mode": "LIVE",
        "native_autoscaler_mode": "OFF",
    }
    scenario_path = tmp_path / "scenarios.json"
    scenario_path.write_text(
        '{"daily_normal":{"phases":[{"name":"one","duration_s":1,'
        '"start_rps":1,"end_rps":1}]}}'
    )

    monkeypatch.setattr(
        experiment,
        "terraform_outputs",
        lambda path: gke_outputs if str(path) == "gke" else agent_outputs,
    )
    monkeypatch.setattr(experiment, "configure_kubectl", lambda outputs: None)
    calls = []

    async def fake_run_one(system, scenario_name, scenario, run_index, outputs, args):
        calls.append((system, run_index))
        return tmp_path

    monkeypatch.setattr(experiment, "run_one", fake_run_one)
    args = type(
        "Args",
        (),
        {
            "system": "both",
            "gke_terraform_dir": "gke",
            "agent_terraform_dir": "agent",
            "allow_agent_dry_run": False,
            "scenarios": scenario_path,
            "scenario": "daily_normal",
            "runs": 2,
        },
    )()

    asyncio.run(experiment.async_main(args))

    assert calls == [("gke", 1), ("agent", 1), ("agent", 2), ("gke", 2)]


def test_require_outputs_lists_missing_values(tmp_path):
    with pytest.raises(RuntimeError, match="missing outputs: region, workload_url"):
        experiment.require_outputs({}, {"region", "workload_url"}, tmp_path)
