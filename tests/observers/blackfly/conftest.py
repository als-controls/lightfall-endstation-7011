import os
import pytest


def pytest_collection_modifyitems(config, items):
    if not os.environ.get("BLACKFLY_TEST_IP"):
        skip_hw = pytest.mark.skip(reason="BLACKFLY_TEST_IP not set")
        for item in items:
            if "hw" in item.keywords:
                item.add_marker(skip_hw)


@pytest.fixture
def camera_ip():
    ip = os.environ.get("BLACKFLY_TEST_IP")
    if not ip:
        pytest.skip("BLACKFLY_TEST_IP not set")
    return ip
