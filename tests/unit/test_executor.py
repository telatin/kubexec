import datetime
import re
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
    return KubeExecutor(config, k8s, user="alice")


def fake_cwd(monkeypatch, path):
    import os
    monkeypatch.setattr(os, "getcwd", lambda: path)


class TestPrepareExecution:
    @pytest.mark.parametrize("cwd, expected", [
        ("/shared/team/proj/x", "/shared/team/proj/x"),
        ("/shared/public/data", "/shared/public/data"),
        ("/shared/team", "/shared/team"),
    ])
    def test_command_runs_in_current_directory_when_shared(self, executor, monkeypatch, cwd, expected):
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


def run_dir(executor, monkeypatch, cwd, explicit=None, run_id="RUN1"):
    with monkeypatch.context() as m:
        fake_cwd(m, cwd)
        return executor._resolve_run_dir(explicit, run_id)


class TestRunDirectory:
    PER_RUN = "/shared/team/kubexec/work/alice/RUN1"

    @pytest.fixture(autouse=True)
    def fixed_run_id(self, executor):
        executor._new_run_id = lambda: "RUN1"

    @pytest.mark.parametrize("cwd", ["/home/someone", "/tmp", "/shared/teamX/proj", "/shared"])
    def test_unshared_cwd_uses_a_new_per_run_folder(self, executor, monkeypatch, cwd):
        assert run_dir(executor, monkeypatch, cwd) == (self.PER_RUN, True, True)

    def test_fallback_command_creates_the_folder_and_removes_it_if_left_empty(self, executor, monkeypatch):
        with monkeypatch.context() as m:
            fake_cwd(m, "/home/someone")
            command, _, _ = executor._prepare_execution("echo hi", "job", "ns", None)
        assert command[2] == (
            f"mkdir -p {self.PER_RUN} && cd {self.PER_RUN} && "
            f"trap 'cd /; rmdir {self.PER_RUN} 2>/dev/null' EXIT && echo hi"
        )

    def test_shared_cwd_is_used_as_is_and_not_created(self, executor, monkeypatch):
        assert run_dir(executor, monkeypatch, "/shared/team/proj") == ("/shared/team/proj", False, False)

    def test_run_ids_are_unique_and_sortable(self):
        ids = {KubeExecutor._new_run_id() for _ in range(200)}
        assert len(ids) == 200
        assert all(re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", i) for i in ids)

    def test_each_job_gets_its_own_folder(self, config, k8s, monkeypatch):
        executor = KubeExecutor(config, k8s, user="alice")
        with monkeypatch.context() as m:
            fake_cwd(m, "/home/x")
            commands = [executor._prepare_execution("echo hi", "job", "ns", None)[0][2] for _ in range(5)]
        folders = {re.search(r"mkdir -p (\S+)", c).group(1) for c in commands}
        assert len(folders) == 5 and all(f.startswith("/shared/team/kubexec/work/alice/") for f in folders)

    def test_configured_default_without_run_placeholder_is_one_shared_folder(self, make_config, k8s, monkeypatch):
        config = make_config("default_workdir: /shared/team/data/{user}/runs\n")
        executor = KubeExecutor(config, k8s, user="alice")
        assert run_dir(executor, monkeypatch, "/home/x") == ("/shared/team/data/alice/runs", True, False)

    def test_user_name_cannot_escape_the_folder(self, config, k8s, monkeypatch):
        executor = KubeExecutor(config, k8s, user="../../etc")
        path, _, _ = run_dir(executor, monkeypatch, "/home/x")
        assert path.startswith("/shared/team/kubexec/work/") and ".." not in path

    def test_explicit_absolute_directory_is_kept_not_cleaned(self, executor, monkeypatch):
        assert run_dir(executor, monkeypatch, "/home/x", "/shared/team/out") == ("/shared/team/out", True, False)
        assert run_dir(executor, monkeypatch, "/shared/team/proj", "/shared/team/out") == ("/shared/team/out", True, False)

    def test_explicit_directory_with_run_placeholder_is_unique_per_run(self, executor, monkeypatch):
        assert run_dir(executor, monkeypatch, "/shared/team/p", "results/{user}/{run}") == (
            "/shared/team/p/results/alice/RUN1", True, True)

    def test_explicit_directory_equal_to_cwd_is_not_created(self, executor, monkeypatch):
        assert run_dir(executor, monkeypatch, "/shared/team/proj", "/shared/team/proj") == ("/shared/team/proj", False, False)

    def test_explicit_relative_directory(self, executor, monkeypatch):
        assert run_dir(executor, monkeypatch, "/shared/team/proj", "results/run1") == (
            "/shared/team/proj/results/run1", True, False)
        # from an unshared cwd it is resolved inside this run's folder
        assert run_dir(executor, monkeypatch, "/home/x", "results") == (f"{self.PER_RUN}/results", True, True)

    def test_explicit_directory_outside_the_team_volume_is_not_created(self, executor, monkeypatch):
        assert run_dir(executor, monkeypatch, "/home/x", "/opt/tool") == ("/opt/tool", False, False)
        assert run_dir(executor, monkeypatch, "/home/x", "/shared/public/data") == ("/shared/public/data", False, False)

    def test_fallback_is_announced_but_a_shared_cwd_is_silent(self, executor, monkeypatch, caplog):
        with caplog.at_level("INFO", logger="kubexec.executor"):
            run_dir(executor, monkeypatch, "/shared/team/proj")
            assert "not on a shared volume" not in caplog.text
            run_dir(executor, monkeypatch, "/home/x")
            assert "not on a shared volume" in caplog.text and self.PER_RUN in caplog.text

    def test_workdir_option_is_the_run_directory_not_the_container_start_dir(self, executor, k8s):
        executor.execute("echo hi", workdir="/shared/team/out")
        assert k8s.create_job.call_args.kwargs["workdir"] == "/tmp"
        command = k8s.create_job.call_args.kwargs["command"]
        assert command[2] == "mkdir -p /shared/team/out && cd /shared/team/out && echo hi"

    def test_dry_run_shows_the_directory(self, executor, monkeypatch):
        with monkeypatch.context() as m:
            fake_cwd(m, "/home/x")
            _, message = executor.execute("echo hi", dry_run=True)
        assert f"cd {self.PER_RUN}" in message


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
class TestCdPrefixBehaviour:
    """Run the generated prefix with real bash against a temp directory"""

    @staticmethod
    def run(prefix, command):
        return subprocess.run(["/bin/bash", "-c", prefix + command], capture_output=True, text=True)

    def test_unique_folder_is_removed_when_nothing_was_written(self, tmp_path):
        folder = tmp_path / "work" / "run"
        result = self.run(KubeExecutor._cd_prefix(str(folder), True, True), "echo hi")
        assert (result.returncode, result.stdout.strip()) == (0, "hi")
        assert not folder.exists()
        assert (tmp_path / "work").exists()

    def test_unique_folder_is_kept_when_output_was_written(self, tmp_path):
        folder = tmp_path / "work" / "run"
        self.run(KubeExecutor._cd_prefix(str(folder), True, True), "echo data > out.txt")
        assert (folder / "out.txt").read_text() == "data\n"

    @pytest.mark.parametrize("command, code", [("exit 3", 3), ("false", 1), ("true", 0)])
    def test_exit_status_is_preserved(self, tmp_path, command, code):
        folder = tmp_path / "run"
        assert self.run(KubeExecutor._cd_prefix(str(folder), True, True), command).returncode == code

    def test_shared_folder_is_not_removed(self, tmp_path):
        folder = tmp_path / "stable"
        self.run(KubeExecutor._cd_prefix(str(folder), True, False), "true")
        assert folder.is_dir()

    def test_awkward_paths_are_quoted(self, tmp_path):
        folder = tmp_path / "it's a run"
        result = self.run(KubeExecutor._cd_prefix(str(folder), True, True), "pwd; touch f")
        assert result.stdout.strip() == str(folder)
        assert (folder / "f").exists()


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

    def test_new_jobs_are_labelled_with_their_owner(self, executor, k8s):
        executor.execute("echo hi")
        assert k8s.create_job.call_args.kwargs["owner"] == "alice"

    def test_default_owner_is_the_current_user(self, config, k8s, monkeypatch):
        monkeypatch.setenv("JUPYTERHUB_USER", "telatin.mmb-dtp")
        KubeExecutor(config, k8s).execute("echo hi")
        assert k8s.create_job.call_args.kwargs["owner"] == "telatin.mmb-dtp"

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
        job.metadata.labels = {"kubexec-user": "alice"}
        k8s.batch_v1.list_namespaced_job.return_value.items = [job]
        jobs = executor.list_jobs("ns")
        assert [j["name"] for j in jobs] == ["a"]
        assert jobs[0]["status"] == "completed"
        assert jobs[0]["user"] == "alice"

    def test_listing_and_cleanup_are_scoped_to_the_current_user_by_default(self, executor, k8s):
        k8s.batch_v1.list_namespaced_job.return_value.items = []
        executor.list_jobs("ns")
        executor.cleanup_old_jobs("ns", 24)
        selectors = [c.kwargs["label_selector"] for c in k8s.batch_v1.list_namespaced_job.call_args_list]
        assert selectors == ["app=kubexec,kubexec-user=alice"] * 2

    def test_all_users_removes_the_user_filter(self, executor, k8s):
        k8s.batch_v1.list_namespaced_job.return_value.items = []
        executor.list_jobs("ns", all_users=True)
        executor.cleanup_old_jobs("ns", 24, all_users=True)
        selectors = [c.kwargs["label_selector"] for c in k8s.batch_v1.list_namespaced_job.call_args_list]
        assert selectors == ["app=kubexec"] * 2

    def test_jobs_without_owner_label_are_reported_as_unknown(self, executor, k8s):
        job = make_job("legacy", 1)
        job.metadata.labels = None
        k8s.batch_v1.list_namespaced_job.return_value.items = [job]
        assert executor.list_jobs("ns", all_users=True)[0]["user"] == "unknown"

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
