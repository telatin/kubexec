import pytest

from kubexec.config import Config


@pytest.fixture(autouse=True)
def default_config_location_is_off_limits(monkeypatch):
    """Unit tests must always pass an explicit config file.

    Resolving the default location (~/.config/kubexec) would read or create files
    in the developer's home and behave differently on CI, so fail loudly instead.
    """
    def refuse(self):
        raise AssertionError("unit tests must pass an explicit config file path")

    monkeypatch.setattr(Config, "_find_config_file", refuse)
