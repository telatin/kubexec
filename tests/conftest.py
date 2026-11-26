"""Shared pytest configuration for kubexec

tests/unit  - no cluster needed, runs anywhere
tests/climb - needs the CLIMB cluster; auto-skipped unless CLIMB is detected
              (set KUBEXEC_TEST_CLIMB=1 / 0 to force on / off)
"""

import os
from pathlib import Path

import pytest

from kubexec.config import Config

SERVICE_ACCOUNT_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")


def detect_climb() -> bool:
    """CLIMB = running in-cluster with the shared CephFS volumes mounted"""
    forced = os.getenv("KUBEXEC_TEST_CLIMB")
    if forced is not None:
        return forced.lower() in ("1", "true", "yes", "on")
    return (
        (SERVICE_ACCOUNT_DIR / "token").exists()
        and Path("/shared/team").is_dir()
        and Path("/shared/public").is_dir()
    )


ON_CLIMB = detect_climb()


def pytest_report_header(config):
    return f"kubexec: CLIMB environment detected: {'yes' if ON_CLIMB else 'no (tests/climb will be skipped)'}"


def pytest_collection_modifyitems(config, items):
    skip_climb = pytest.mark.skip(reason="not running on CLIMB (set KUBEXEC_TEST_CLIMB=1 to force)")
    for item in items:
        if Path("tests", "climb") in Path(os.path.relpath(str(item.path))).parents:
            item.add_marker(pytest.mark.climb)
            if not ON_CLIMB:
                item.add_marker(skip_climb)


@pytest.fixture(autouse=True)
def clean_kubexec_env(monkeypatch):
    """Make sure the developer's KUBEXEC_* variables never leak into tests"""
    for name in list(os.environ):
        if name.startswith("KUBEXEC_") and name != "KUBEXEC_TEST_CLIMB":
            monkeypatch.delenv(name)


@pytest.fixture
def make_config(tmp_path, monkeypatch):
    """Factory for a Config backed by a temp file, without calling kubectl"""
    monkeypatch.setattr(Config, "_detect_namespace", lambda self: "test-ns")

    def _make(yaml_text=None):
        path = tmp_path / "config.yaml"
        if yaml_text is not None:
            path.write_text(yaml_text)
        return Config(str(path))

    return _make


@pytest.fixture
def config(make_config):
    return make_config()
