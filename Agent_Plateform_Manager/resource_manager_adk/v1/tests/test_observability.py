from resource_manager.observability import cloud_trace_requested


def test_cloud_trace_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ADK_CLOUD_TRACE_ENABLED", raising=False)

    assert cloud_trace_requested() is False


def test_cloud_trace_accepts_true_environment_value(monkeypatch):
    monkeypatch.setenv("ADK_CLOUD_TRACE_ENABLED", "true")

    assert cloud_trace_requested() is True
