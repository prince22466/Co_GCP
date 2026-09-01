"""Google ADK observability configuration for the Cloud Run runtime."""

from __future__ import annotations

import os


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def cloud_trace_requested() -> bool:
    """Return whether this process should export ADK traces to Cloud Trace."""
    configured = os.getenv("ADK_CLOUD_TRACE_ENABLED", "false").strip().lower()
    return configured in _TRUE_VALUES


def configure_adk_cloud_trace() -> bool:
    """Configure ADK's OpenTelemetry exporter before the first agent run."""
    if not cloud_trace_requested():
        return False

    from google.adk.telemetry.google_cloud import get_gcp_exporters
    from google.adk.telemetry.setup import maybe_set_otel_providers

    exporters = get_gcp_exporters(enable_cloud_tracing=True)
    maybe_set_otel_providers(otel_hooks_to_setup=[exporters])
    return True
