import pytest

from kubexec.config import Config


@pytest.fixture(autouse=True)
def default_config_location_is_unusable(tmp_path, monkeypatch):
    """Unit tests must always pass an explicit config file.

    Falling back to the default location (/shared/team/kubexec/..., ~/.config/...)
    would work on a developer machine or CLIMB but fail on CI, so make any such
    fallback fail everywhere.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    monkeypatch.setattr(Config, "DEFAULT_CONFIG_DIRS", [str(blocker / "kubexec")])
