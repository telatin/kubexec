"""End-to-end tests that create real (short-lived) jobs on CLIMB"""

import time

import pytest
from kubernetes.client.rest import ApiException

from kubexec.executor import KubeExecutor

from .conftest import REPO_ROOT

pytestmark = pytest.mark.e2e


@pytest.fixture
def executor(k8s, real_config, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)  # jobs `cd` into the caller's directory on the shared volume
    return KubeExecutor(real_config, k8s)


def wait_until_job_gone(k8s, name, namespace, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            k8s.batch_v1.read_namespaced_job(name, namespace)
        except ApiException as e:
            if e.status == 404:
                return True
            raise
        time.sleep(2)
    return False


def test_command_runs_and_job_is_cleaned_up(executor, k8s, namespace, monkeypatch):
    created = []
    original = k8s.create_job
    monkeypatch.setattr(k8s, "create_job", lambda **kw: created.append(kw["name"]) or original(**kw))

    code, logs = executor.execute("echo kubexec-e2e-ok && pwd", cleanup=True)

    assert code == 0
    assert "kubexec-e2e-ok" in logs
    assert str(REPO_ROOT) in logs
    assert len(created) == 1
    assert wait_until_job_gone(k8s, created[0], namespace), "job was not cleaned up"


def test_failing_command_returns_nonzero(executor):
    code, logs = executor.execute("echo about-to-fail; exit 3", cleanup=True)
    assert code != 0
    assert "about-to-fail" in logs


def test_script_file_runs(executor, tmp_path):
    script = tmp_path / "e2e_script.sh"
    script.write_text("#!/bin/bash\necho script-says-$((6 * 7))\n")
    code, logs = executor.execute(str(script), cleanup=True)
    assert code == 0
    assert "script-says-42" in logs


def test_shared_team_volume_is_mounted(executor):
    code, logs = executor.execute(f"test -f {REPO_ROOT}/setup.py && echo setup-visible", cleanup=True)
    assert code == 0
    assert "setup-visible" in logs
