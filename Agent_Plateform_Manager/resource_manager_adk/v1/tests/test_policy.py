import pytest

from resource_manager.policy import recommend_capacity


def test_scales_up_when_cpu_is_high():
    result = recommend_capacity(2, 0.73, 200, 0.0)

    assert result["action"] == "scale_up"
    assert result["proposed_units"] == 3


def test_scales_up_when_latency_slo_is_breached():
    result = recommend_capacity(2, 0.40, 650, 0.0)

    assert result["action"] == "scale_up"
    assert result["proposed_units"] == 3


def test_does_not_exceed_maximum():
    result = recommend_capacity(4, 0.90, 700, 0.03)

    assert result["action"] == "hold_at_max"
    assert result["proposed_units"] == 4


def test_scales_down_only_when_slos_are_met():
    result = recommend_capacity(3, 0.20, 150, 0.0)

    assert result["action"] == "scale_down"
    assert result["proposed_units"] == 2


def test_holds_during_cooldown():
    result = recommend_capacity(2, 0.90, 700, 0.03, cooldown_active=True)

    assert result["action"] == "hold_cooldown"
    assert result["proposed_units"] == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"current_units": 0, "average_cpu_ratio": 0.5, "p99_latency_ms": 100, "error_rate": 0},
        {"current_units": 1, "average_cpu_ratio": -0.1, "p99_latency_ms": 100, "error_rate": 0},
        {"current_units": 1, "average_cpu_ratio": 0.5, "p99_latency_ms": -1, "error_rate": 0},
        {"current_units": 1, "average_cpu_ratio": 0.5, "p99_latency_ms": 100, "error_rate": 1.1},
    ],
)
def test_rejects_invalid_metrics(kwargs):
    with pytest.raises(ValueError):
        recommend_capacity(**kwargs)

