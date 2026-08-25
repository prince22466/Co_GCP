from resource_manager import __version__
from resource_manager.agent import AGENT_VERSION


def test_v1_release_identity():
    assert __version__ == "1.0.0"
    assert AGENT_VERSION == "v1"
