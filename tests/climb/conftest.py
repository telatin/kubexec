from pathlib import Path

import pytest

from kubexec.config import Config
from kubexec.k8s_client import KubernetesClient

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def k8s():
    return KubernetesClient()


@pytest.fixture
def real_config(tmp_path):
    """Config with real namespace detection, but a throwaway file and short timeout"""
    config = Config(str(tmp_path / "config.yaml"))
    config.update({"timeout": 300})
    return config


@pytest.fixture
def namespace(real_config):
    return real_config.get("namespace")
