import sys
from unittest.mock import MagicMock

import pytest

from kubexec import kuberlist


@pytest.fixture
def fake_k8s(monkeypatch):
    instance = MagicMock()
    instance.core_v1.list_namespaced_pod.return_value.items = []
    factory = MagicMock(return_value=instance)
    monkeypatch.setattr(kuberlist, "KubernetesClient", factory)
    return factory


@pytest.fixture
def run_kuberlist(monkeypatch, make_config):
    def _run(*argv):
        config = make_config()
        monkeypatch.setattr(sys, "argv", ["kuberlist", "--config", config.config_file, *argv])
        return kuberlist.main()
    return _run


def make_pod(name, user):
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.labels = {"kubexec-user": user}
    pod.metadata.creation_timestamp = None
    pod.status.phase = "Running"
    pod.status.container_statuses = []
    pod.spec.node_name = "node-1"
    pod.spec.containers = [MagicMock(image="alpine")]
    return pod


class TestPodSelector:
    def test_no_filter_by_default(self):
        assert kuberlist.pod_selector(False) is None
        assert kuberlist.pod_selector(False, all_users=True) is None

    def test_kubexec_is_scoped_to_the_user(self):
        assert kuberlist.pod_selector(True, user="alice") == "app=kubexec,kubexec-user=alice"

    def test_all_users_drops_the_user_filter(self):
        assert kuberlist.pod_selector(True, all_users=True, user="alice") == "app=kubexec"


class TestMain:
    def test_parser_flags(self):
        args = kuberlist.create_parser().parse_args(["--kubexec", "--all-users", "--config", "x.yaml"])
        assert args.kubexec and args.all_users and args.config == "x.yaml"

    def test_honours_config_namespace(self, monkeypatch, make_config, fake_k8s):
        config = make_config("namespace: from-config\n")
        monkeypatch.setattr(sys, "argv", ["kuberlist", "--config", config.config_file])
        assert kuberlist.main() == 0
        assert fake_k8s.return_value.core_v1.list_namespaced_pod.call_args.kwargs["namespace"] == "from-config"

    def test_kubexec_lists_only_own_pods(self, run_kuberlist, fake_k8s, monkeypatch):
        monkeypatch.setenv("JUPYTERHUB_USER", "telatin.mmb-dtp")
        list_pods = fake_k8s.return_value.core_v1.list_namespaced_pod
        assert run_kuberlist("--kubexec") == 0
        assert list_pods.call_args.kwargs["label_selector"] == "app=kubexec,kubexec-user=telatin.mmb-dtp"

        assert run_kuberlist("--kubexec", "--all-users") == 0
        assert list_pods.call_args.kwargs["label_selector"] == "app=kubexec"

    def test_plain_listing_is_unfiltered(self, run_kuberlist, fake_k8s):
        assert run_kuberlist() == 0
        assert fake_k8s.return_value.core_v1.list_namespaced_pod.call_args.kwargs["label_selector"] is None

    def test_all_users_shows_owner_column(self, run_kuberlist, fake_k8s, capsys):
        fake_k8s.return_value.core_v1.list_namespaced_pod.return_value.items = [make_pod("p1", "bob")]
        assert run_kuberlist("--kubexec", "--all-users") == 0
        out = capsys.readouterr().out
        assert "USER" in out and "bob" in out

    def test_own_listing_has_no_owner_column(self, run_kuberlist, fake_k8s, capsys):
        fake_k8s.return_value.core_v1.list_namespaced_pod.return_value.items = [make_pod("p1", "bob")]
        assert run_kuberlist("--kubexec", "--all", "--running") == 0
        out = capsys.readouterr().out
        assert "p1" in out and "USER" not in out

    def test_empty_own_listing_hints_at_all_users(self, run_kuberlist, fake_k8s, capsys):
        assert run_kuberlist("--kubexec") == 0
        assert "--all-users" in capsys.readouterr().out
