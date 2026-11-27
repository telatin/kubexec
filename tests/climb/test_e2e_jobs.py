"""End-to-end tests that create real (short-lived) jobs on CLIMB"""

import shutil
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


def test_job_is_labelled_with_the_current_user(executor, k8s, namespace, monkeypatch):
    from kubexec.templates import OWNER_LABEL
    from kubexec.utils import get_user_identity

    created = []
    original = k8s.create_job
    monkeypatch.setattr(k8s, "create_job", lambda **kw: created.append(kw["name"]) or original(**kw))

    code, _ = executor.execute("true", cleanup=False)
    try:
        assert code == 0
        job = k8s.batch_v1.read_namespaced_job(created[0], namespace)
        assert job.metadata.labels[OWNER_LABEL] == get_user_identity()
        assert [j["name"] for j in executor.list_jobs(namespace) if j["name"] == created[0]] == created
    finally:
        k8s.cleanup_job(created[0], namespace)


class TestPersistentWorkdir:
    """From a directory the job cannot see, results must land in a persistent per-user folder"""

    @pytest.fixture
    def work_root(self):
        root = REPO_ROOT / ".e2e-work"
        shutil.rmtree(root, ignore_errors=True)
        yield root
        shutil.rmtree(root, ignore_errors=True)

    @pytest.fixture
    def from_outside(self, k8s, real_config, work_root, tmp_path, monkeypatch):
        from kubexec.executor import on_shared_volume
        assert not on_shared_volume(str(tmp_path)), "tmp_path unexpectedly on a shared volume"
        monkeypatch.chdir(tmp_path)
        real_config.update({"default_workdir": f"{work_root}/{{user}}"})
        return KubeExecutor(real_config, k8s)

    def test_output_is_kept_in_the_per_user_folder(self, from_outside, work_root):
        from kubexec.utils import get_user_identity

        code, logs = from_outside.execute("echo persisted > out.txt && pwd", cleanup=True)

        folder = work_root / get_user_identity()
        assert code == 0
        assert logs.strip() == str(folder)
        assert (folder / "out.txt").read_text().strip() == "persisted"

    def test_explicit_workdir_overrides_the_default(self, from_outside, work_root):
        target = work_root / "explicit" / "run1"
        code, logs = from_outside.execute("echo here > f.txt && pwd", workdir=str(target), cleanup=True)
        assert code == 0
        assert logs.strip() == str(target)
        assert (target / "f.txt").read_text().strip() == "here"


class TestUniqueRunFolders:
    """The default: every run gets its own folder, so runs can never overwrite each other"""

    @pytest.fixture
    def work_root(self):
        root = REPO_ROOT / ".e2e-work"
        shutil.rmtree(root, ignore_errors=True)
        yield root
        shutil.rmtree(root, ignore_errors=True)

    @pytest.fixture
    def executor_outside(self, k8s, real_config, work_root, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # not visible to jobs, so the default_workdir applies
        real_config.update({"default_workdir": f"{work_root}/{{user}}/{{run}}"})
        return KubeExecutor(real_config, k8s)

    @staticmethod
    def user_dir(work_root):
        from kubexec.utils import get_user_identity
        return work_root / get_user_identity()

    def test_parallel_jobs_writing_the_same_file_do_not_collide(self, executor_outside, work_root):
        from concurrent.futures import ThreadPoolExecutor

        def run(n):
            return executor_outside.execute(f"echo job-{n} > out.txt; sleep 3", cleanup=True)

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(run, range(3)))

        assert [code for code, _ in results] == [0, 0, 0]
        folders = sorted(self.user_dir(work_root).iterdir())
        assert len(folders) == 3
        assert sorted((f / "out.txt").read_text().strip() for f in folders) == ["job-0", "job-1", "job-2"]

    def test_run_that_writes_nothing_leaves_no_folder_behind(self, executor_outside, work_root):
        code, logs = executor_outside.execute("echo only-prints", cleanup=True)
        assert code == 0 and "only-prints" in logs
        assert list(self.user_dir(work_root).iterdir()) == []

    def test_failing_run_keeps_its_output_and_its_exit_code(self, executor_outside, work_root):
        code, logs = executor_outside.execute("echo partial > out.txt; exit 3", cleanup=True)
        assert code != 0
        (folder,) = list(self.user_dir(work_root).iterdir())
        assert (folder / "out.txt").read_text().strip() == "partial"
