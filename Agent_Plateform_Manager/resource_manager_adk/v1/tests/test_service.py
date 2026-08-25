from resource_manager.service import service_app


def test_cloud_run_routes_are_registered():
    paths = {route.path for route in service_app.routes}

    assert "/healthz" in paths
    assert "/evaluate" in paths

