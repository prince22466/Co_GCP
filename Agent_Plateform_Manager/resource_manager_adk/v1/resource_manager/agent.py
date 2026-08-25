"""Advisory resource-management agent built with Google ADK."""

import os

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from .cloud_tools import manage_mig_capacity
from .policy import recommend_capacity


MODEL = os.getenv("ADK_MODEL", "gemini-3.6-flash")
AGENT_VERSION = os.getenv("AGENT_VERSION", "v1")


def assess_capacity(
    current_units: int,
    average_cpu_ratio: float,
    p99_latency_ms: float,
    error_rate: float,
    cooldown_active: bool = False,
) -> dict[str, object]:
    """Recommend the next MIG capacity from one metrics snapshot.

    Args:
        current_units: Current MIG size, from 1 through 4.
        average_cpu_ratio: Average VM CPU as a ratio; 0.73 means 73%.
        p99_latency_ms: Observed p99 request latency in milliseconds.
        error_rate: Failed requests divided by attempted requests; 0.01 means 1%.
        cooldown_active: Whether a recent resize still blocks another action.

    Returns:
        An advisory action, bounded proposed capacity, reasons, and policy values.
        The tool never resizes infrastructure.
    """
    return recommend_capacity(
        current_units=current_units,
        average_cpu_ratio=average_cpu_ratio,
        p99_latency_ms=p99_latency_ms,
        error_rate=error_rate,
        cooldown_active=cooldown_active,
    )


root_agent = Agent(
    name="resource_manager_agent_v1",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description=(
        f"Resource Manager {AGENT_VERSION}: evaluates and safely manages the "
        "experiment's Compute Engine MIG capacity."
    ),
    instruction="""
You are the resource manager for the Agentic Cloud Resource Manager experiment.

For a scheduled capacity evaluation, call manage_mig_capacity exactly once. That
tool reads fresh GCP metrics, enforces all capacity and cooldown guardrails, and
performs the resize only when live scaling is enabled. Never invent metrics or a
replica count and never claim a resize unless the tool reports applied=true.

For an interactive what-if question containing a complete metrics snapshot, use
assess_capacity instead.

Rules:
- Treat CPU and error rate inputs as ratios: 0.73 is 73%, and 0.01 is 1%.
- Ask for any missing current capacity, CPU, p99 latency, or error-rate value.
- Base the recommendation on the tool result; do not invent thresholds.
- State the action, current units, proposed units, and concise reasons.
- If a tool reports dry_run, clearly say that no infrastructure was changed.
""".strip(),
    tools=[manage_mig_capacity, assess_capacity],
)

app = App(root_agent=root_agent, name="resource_manager_v1")
