"""Deterministic capacity guardrails used by the ADK agent."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CapacityRecommendation:
    action: str
    current_units: int
    proposed_units: int
    reasons: list[str]
    policy: dict[str, float | int]


def recommend_capacity(
    current_units: int,
    average_cpu_ratio: float,
    p99_latency_ms: float | None,
    error_rate: float | None,
    cooldown_active: bool = False,
    min_units: int = 1,
    max_units: int = 4,
    scale_up_cpu: float = 0.65,
    scale_down_cpu: float = 0.30,
    slo_p99_ms: float = 500.0,
    slo_max_error_rate: float = 0.01,
) -> dict[str, object]:
    """Return a bounded, deterministic scaling recommendation.

    CPU and error rate are ratios, so 0.73 means 73% CPU and 0.01 means a
    1% error rate. This function never changes infrastructure.
    """
    if min_units < 1 or max_units < min_units:
        raise ValueError("capacity bounds must satisfy 1 <= min_units <= max_units")
    if not min_units <= current_units <= max_units:
        raise ValueError("current_units must be within the configured bounds")
    if average_cpu_ratio < 0:
        raise ValueError("average_cpu_ratio must be non-negative")
    if p99_latency_ms is not None and p99_latency_ms < 0:
        raise ValueError("p99_latency_ms must be non-negative")
    if error_rate is not None and not 0 <= error_rate <= 1:
        raise ValueError("error_rate must be between 0 and 1")

    policy: dict[str, float | int] = {
        "min_units": min_units,
        "max_units": max_units,
        "scale_up_cpu": scale_up_cpu,
        "scale_down_cpu": scale_down_cpu,
        "slo_p99_ms": slo_p99_ms,
        "slo_max_error_rate": slo_max_error_rate,
    }
    reasons: list[str] = []

    if cooldown_active:
        recommendation = CapacityRecommendation(
            action="hold_cooldown",
            current_units=current_units,
            proposed_units=current_units,
            reasons=["A scaling cooldown is active."],
            policy=policy,
        )
        return asdict(recommendation)

    if average_cpu_ratio > scale_up_cpu:
        reasons.append(
            f"CPU {average_cpu_ratio:.1%} is above the {scale_up_cpu:.1%} scale-up threshold."
        )
    if p99_latency_ms is not None and p99_latency_ms > slo_p99_ms:
        reasons.append(
            f"p99 latency {p99_latency_ms:.1f} ms is above the {slo_p99_ms:.1f} ms SLO."
        )
    if error_rate is not None and error_rate > slo_max_error_rate:
        reasons.append(
            f"Error rate {error_rate:.2%} is above the {slo_max_error_rate:.2%} SLO."
        )

    if reasons:
        proposed_units = min(max_units, current_units + 1)
        action = "scale_up" if proposed_units > current_units else "hold_at_max"
        if action == "hold_at_max":
            reasons.append("Capacity is already at the configured maximum.")
    elif average_cpu_ratio < scale_down_cpu:
        proposed_units = max(min_units, current_units - 1)
        action = "scale_down" if proposed_units < current_units else "hold_at_min"
        slo_note = (
            "and both SLOs are met"
            if p99_latency_ms is not None and error_rate is not None
            else "and no available signal shows an SLO breach"
        )
        reasons.append(
            f"CPU {average_cpu_ratio:.1%} is below the {scale_down_cpu:.1%} scale-down threshold {slo_note}."
        )
        if action == "hold_at_min":
            reasons.append("Capacity is already at the configured minimum.")
    else:
        proposed_units = current_units
        action = "hold"
        reasons.append("CPU is within the hold band and no available signal shows an SLO breach.")

    if p99_latency_ms is None or error_rate is None:
        reasons.append(
            "Latency or error-rate telemetry was unavailable; this recommendation used CPU telemetry only."
        )

    return asdict(
        CapacityRecommendation(
            action=action,
            current_units=current_units,
            proposed_units=proposed_units,
            reasons=reasons,
            policy=policy,
        )
    )
