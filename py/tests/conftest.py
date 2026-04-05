import shutil

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip integration tests by default unless -m integration is passed."""
    if config.getoption("-m"):
        return
    skip = pytest.mark.skip(reason="needs -m integration")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def has_claude():
    """Return True if the `claude` binary is on PATH."""
    return shutil.which("claude") is not None
