import datetime
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from kubernetes.client.rest import ApiException

from kubexec.exceptions import JobExecutionError, KubernetesClientError
import kubexec.executor as executor_module
from kubexec.executor import KubeExecutor


@pytest.fixture
def k8s():
    client = MagicMock()
    client.pod_exists.return_value = False
    client.wait_for_job_completion.return_value = (0, "job output")
    return client


@pytest.fixture
def executor(config, k8s):
    return KubeExecutor(config, k8s)


def fake_cwd(monkeypatch, path):
    import os
    monkeypatch.setattr(os, "getcwd", lambda: path)


class TestPrepareExecution:
    @pytest.mark.parametrize("cwd, expected", [
        ("/shared/team/proj/x", "/shared/team/proj/x"),
        ("/shared/public/data", "/shared/public/data"),
        ("/home/someone", "/shared/team"),
    ])
    def test_command_runs_in_mapped_directory(self, executor, monkeypatch, cwd, expected):
        with monkeypatch.context() as m:
            fake_cwd(m, cwd)
            command, _, _ = executor._prepare_execution("echo hi", "job", "ns", None)
        assert command == ["/bin/bash", "-c", f"cd {expected} && echo hi"]

    def test_shared_volumes_always_mounted(self, executor):
        _, volumes, mounts = executor._prepare_execution("echo hi", "job", "ns", None)
        assert {m["mountPath"] for m in mounts} == {"/shared/team", "/shared/public"}
        assert {v["name"] for v in volumes} == {m["name"] for m in mounts}

    def test_custom_volume_mounts(self, executor):
        _, volumes, mounts = executor._prepare_execution("echo hi", "job", "ns", ["/data:/mnt/data:ro"])
        assert {"name": "custom-volume-2", "hostPath": {"path": "/data"}} in volumes
        assert {"name": "custom-volume-2", "mountPath": "/mnt/data", "readOnly": True} in mounts

    def test_script_file_is_embedded(self, executor, tmp_path):
        script = tmp_path / "job.sh"
        script.write_text("#!/bin/bash\necho from-script\n")
        command, _, _ = executor._prepare_execution(str(script), "job", "ns", None)
        assert command[:2] == ["/bin/bash", "-c"]
        assert "echo from-script" in command[2]
        assert "chmod +x /tmp/job.sh && /tmp/job.sh" in command[2]


class TestExecute:
    def test_dry_run_does_not_touch_cluster(self, executor, k8s):
        code, msg = executor.execute("echo hi", dry_run=True)
        assert code == 0
        assert msg.startswith("Would create job kubexec-job-")
        k8s.create_job.assert_not_called()

    def test_new_job_uses_config_defaults_and_cleans_up(self, executor, k8s):
        code, logs = executor.execute("echo hi")
        assert (code, logs) == (0, "job output")
        kwargs = k8s.create_job.call_args.kwargs
        assert kwargs["image"] == "ubuntu:latest"
        assert kwargs["namespace"] == "test-ns"
        assert kwargs["memory"] == "1Gi"
        k8s.cleanup_job.assert_called_once()

    def test_overrides_and_no_cleanup(self, executor, k8s):
        executor.execute("echo hi", docker_image="alpine", namespace="other", memory="2Gi", cleanup=False)
        kwargs = k8s.create_job.call_args.kwargs
        assert (kwargs["image"], kwargs["namespace"], kwargs["memory"]) == ("alpine", "other", "2Gi")
        k8s.cleanup_job.assert_not_called()

    def test_failure_is_wrapped_and_still_cleaned_up(self, executor, k8s):
        k8s.create_job.side_effect = RuntimeError("boom")
        with pytest.raises(JobExecutionError, match="boom"):
            executor.execute("echo hi")
        k8s.cleanup_job.assert_called_once()

    def test_existing_pod_is_used_when_found(self, executor, k8s):
        k8s.pod_exists.return_value = True
        k8s.execute_in_existing_pod.return_value = (0, "in pod")
        assert executor.execute("echo hi", pod_name="mypod") == (0, "in pod")
        k8s.create_job.assert_not_called()

    def test_create_pod_flag_forces_new_job(self, executor, k8s):
        k8s.pod_exists.return_value = True
        executor.execute("echo hi", pod_name="mypod", create_pod=True)
        k8s.create_job.assert_called_once()
        k8s.execute_in_existing_pod.assert_not_called()

    def test_invalid_resources_rejected_before_any_api_call(self, executor, k8s):
        with pytest.raises(ValueError):
            executor.execute("echo hi", cpu="fast")
        k8s.create_job.assert_not_called()


def make_job(name, age_hours):
    job = MagicMock()
    job.metadata.name = name
    job.metadata.creation_timestamp = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=age_hours)
    )
    return job


class TestJobManagement:
    def test_list_jobs(self, executor, k8s):
        job = make_job("a", 1)
        job.status.succeeded = 1
        k8s.batch_v1.list_namespaced_job.return_value.items = [job]
        jobs = executor.list_jobs("ns")
        assert [j["name"] for j in jobs] == ["a"]
        assert jobs[0]["status"] == "completed"
        assert k8s.batch_v1.list_namespaced_job.call_args.kwargs["label_selector"] == "app=kubexec"

    def test_cleanup_old_jobs_only_removes_old(self, executor, k8s):
        k8s.batch_v1.list_namespaced_job.return_value.items = [make_job("old", 48), make_job("new", 1)]
        assert executor.cleanup_old_jobs("ns", 24) == 1
        k8s.cleanup_job.assert_called_once_with("old", "ns")

    @pytest.mark.parametrize("method", ["list_jobs", "cleanup_old_jobs"])
    def test_api_errors_are_wrapped(self, executor, k8s, method):
        k8s.batch_v1.list_namespaced_job.side_effect = ApiException(status=403)
        with pytest.raises(KubernetesClientError):
            getattr(executor, method)("ns")


TRICKY_SCRIPT = (
    "cat <<'EOF'\n"
    "hello $USER-literal\n"
    "KUBEXEC_SCRIPT_EOF\n"
    "EOF\n"
    "cat <<EOF2\nsecond\nEOF2\n"
    "echo 'single quoted'\n"
    "echo done"
)


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
class TestScriptWrapper:
    """Run the generated shell snippet with real bash against a local /tmp"""

    @pytest.fixture
    def script(self, tmp_path):
        # unique name: the snippet writes to /tmp/<name>
        path = tmp_path / f"kubexec_test_{tmp_path.name}.sh"
        yield path
        (Path("/tmp") / path.name).unlink(missing_ok=True)

    @staticmethod
    def run(snippet):
        return subprocess.run(["/bin/bash", "-c", snippet], capture_output=True, text=True)

    def test_script_with_heredocs_and_old_delimiters_runs_intact(self, executor, script):
        script.write_text(TRICKY_SCRIPT)
        result = self.run(executor._script_shell_command(str(script)))

        assert result.stdout.splitlines() == [
            "hello $USER-literal", "KUBEXEC_SCRIPT_EOF", "second", "single quoted", "done",
        ]
        assert "command not found" not in result.stderr

    def test_written_file_matches_original(self, executor, script):
        script.write_text(TRICKY_SCRIPT)
        self.run(executor._script_shell_command(str(script)))
        assert (Path("/tmp") / script.name).read_text() == TRICKY_SCRIPT + "\n"

    def test_script_without_trailing_newline(self, executor, script):
        script.write_text("echo no-newline")
        result = self.run(executor._script_shell_command(str(script)))
        assert result.stdout.strip() == "no-newline"

    def test_script_name_with_spaces_and_quotes(self, executor, tmp_path):
        script = tmp_path / "my 'odd' script.sh"
        script.write_text("echo ok\n")
        try:
            result = self.run(executor._script_shell_command(str(script)))
        finally:
            (Path("/tmp") / script.name).unlink(missing_ok=True)
        assert result.stdout.strip() == "ok"

    def test_delimiter_is_regenerated_on_collision(self, executor, script, monkeypatch):
        script.write_text("echo KUBEXEC_EOF_aaaaaaaa\n")
        ids = iter([SimpleNamespace(hex="aaaaaaaa" + "0" * 24), SimpleNamespace(hex="bbbbbbbb" + "0" * 24)])
        monkeypatch.setattr(executor_module.uuid, "uuid4", lambda: next(ids))
        snippet = executor._script_shell_command(str(script))
        assert "<< 'KUBEXEC_EOF_bbbbbbbb'" in snippet
        assert self.run(snippet).stdout.strip() == "KUBEXEC_EOF_aaaaaaaa"

    def test_existing_pod_path_uses_the_wrapper(self, executor, k8s, script):
        script.write_text(TRICKY_SCRIPT)
        k8s.execute_in_existing_pod.return_value = (0, "")
        executor._execute_in_existing_pod(str(script), "pod", "ns", dry_run=False)
        command = k8s.execute_in_existing_pod.call_args.args[1]
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.stdout.splitlines()[-1] == "done"


def test_new_job_script_command_has_cd_prefix_and_wrapper(executor, tmp_path, monkeypatch):
    script = tmp_path / "job.sh"
    script.write_text("cat <<EOF\nx\nEOF\n")
    with monkeypatch.context() as m:
        fake_cwd(m, "/shared/team/my dir")
        command, _, _ = executor._prepare_execution(str(script), "job", "ns", None)
    assert command[2].startswith("cd '/shared/team/my dir' && cat << 'KUBEXEC_EOF_")
    assert command[2].endswith("chmod +x /tmp/job.sh && /tmp/job.sh")


def test_command_path_is_shell_quoted(executor, monkeypatch):
    with monkeypatch.context() as m:
        fake_cwd(m, "/shared/team/it's here")
        command, _, _ = executor._prepare_execution("echo hi", "job", "ns", None)
    assert command[2] == "cd '/shared/team/it'\"'\"'s here' && echo hi"
