import sys
from unittest.mock import MagicMock

import pytest

from kubexec import __version__, cli


@pytest.fixture
def fake_k8s(monkeypatch):
    """Patch the KubernetesClient class used by the CLI"""
    instance = MagicMock()
    instance.pod_exists.return_value = False
    instance.batch_v1.list_namespaced_job.return_value.items = []
    factory = MagicMock(return_value=instance)
    monkeypatch.setattr(cli, "KubernetesClient", factory)
    return factory


@pytest.fixture
def run_cli(monkeypatch, make_config):
    def _run(*argv, config_yaml=None):
        config = make_config(config_yaml)
        monkeypatch.setattr(sys, "argv", ["kubexec", "--config", config.config_file, *argv])
        return cli.main()
    return _run


class TestParser:
    def test_target_is_optional(self):
        assert cli.create_parser().parse_args([]).target is None

    def test_flags(self):
        args = cli.create_parser().parse_args(
            ["-d", "alpine", "-n", "ns", "-v", "/a:/b", "-v", "/c:/d", "--dry-run", "echo hi"]
        )
        assert (args.docker_image, args.namespace, args.target) == ("alpine", "ns", "echo hi")
        assert args.volume_mounts == ["/a:/b", "/c:/d"]
        assert args.dry_run is True

    def test_list_jobs_needs_no_target(self):
        args = cli.create_parser().parse_args(["--list-jobs"])
        assert args.list_jobs and args.target is None

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.create_parser().parse_args(["--version"])
        assert exc.value.code == 0
        assert __version__ in capsys.readouterr().out


class TestMain:
    def test_missing_target_is_an_error(self, run_cli, fake_k8s, capsys):
        with pytest.raises(SystemExit) as exc:
            run_cli()
        assert exc.value.code == 2
        assert "TARGET argument is required" in capsys.readouterr().err

    def test_list_jobs_without_target(self, run_cli, fake_k8s, capsys):
        assert run_cli("--list-jobs") == 0
        assert "No kubexec jobs of yours found" in capsys.readouterr().out

    def test_cleanup_old_without_target(self, run_cli, fake_k8s, capsys):
        assert run_cli("--cleanup-old", "24") == 0
        assert "Cleaned up 0 jobs" in capsys.readouterr().out

    def test_jobs_commands_are_scoped_to_the_user_unless_all_users(self, run_cli, fake_k8s, monkeypatch):
        monkeypatch.setenv("JUPYTERHUB_USER", "telatin.mmb-dtp")
        list_jobs = fake_k8s.return_value.batch_v1.list_namespaced_job

        run_cli("--list-jobs")
        run_cli("--cleanup-old", "24")
        assert [c.kwargs["label_selector"] for c in list_jobs.call_args_list] == [
            "app=kubexec,kubexec-user=telatin.mmb-dtp"
        ] * 2

        list_jobs.reset_mock()
        run_cli("--list-jobs", "--all-users")
        run_cli("--cleanup-old", "24", "--all-users")
        assert [c.kwargs["label_selector"] for c in list_jobs.call_args_list] == ["app=kubexec"] * 2

    def test_all_users_listing_shows_owner_column(self, run_cli, fake_k8s, capsys):
        job = MagicMock()
        job.metadata.name = "kubexec-job-1"
        job.metadata.labels = {"kubexec-user": "bob"}
        job.status.succeeded = 1
        job.spec.template.spec.containers = [MagicMock(image="alpine")]
        fake_k8s.return_value.batch_v1.list_namespaced_job.return_value.items = [job]
        assert run_cli("--list-jobs", "--all-users") == 0
        out = capsys.readouterr().out
        assert "USER" in out and "bob" in out

    def test_dry_run(self, run_cli, fake_k8s, capsys):
        assert run_cli("--dry-run", "-d", "alpine", "echo hi") == 0
        out = capsys.readouterr().out
        assert "Would create job" in out and "alpine" in out and "echo hi" in out

    def test_tls_verification_follows_config(self, run_cli, fake_k8s):
        run_cli("--dry-run", "echo hi")
        assert fake_k8s.call_args.kwargs["verify_ssl"] is True

        run_cli("--dry-run", "echo hi", config_yaml="verify_ssl: false\n")
        assert fake_k8s.call_args.kwargs["verify_ssl"] is False

    def test_workdir_option_selects_the_run_directory(self, run_cli, fake_k8s, capsys):
        assert run_cli("--dry-run", "-w", "/shared/team/results", "echo hi") == 0
        assert "cd /shared/team/results && echo hi" in capsys.readouterr().out

    def test_job_exit_code_is_propagated(self, run_cli, fake_k8s):
        fake_k8s.return_value.wait_for_job_completion.return_value = (3, "")
        assert run_cli("false") == 3

    def test_kubexec_errors_return_1(self, run_cli, fake_k8s):
        fake_k8s.return_value.create_job.side_effect = RuntimeError("boom")
        assert run_cli("echo hi") == 1
