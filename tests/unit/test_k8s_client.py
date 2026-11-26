import logging
import ssl
from unittest.mock import MagicMock

import pytest
from kubernetes.client.rest import ApiException

from kubexec import k8s_client as k8s_module
from kubexec.exceptions import JobExecutionError, KubernetesClientError
from kubexec.k8s_client import KubernetesClient
from kubexec.templates import create_job_template


@pytest.fixture(autouse=True)
def no_kube_config(monkeypatch):
    monkeypatch.setattr(k8s_module.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(k8s_module.config, "load_kube_config", lambda **kw: None)


@pytest.fixture
def mocked_api():
    """A client whose API objects are replaced by mocks"""
    c = KubernetesClient()
    c.batch_v1 = MagicMock()
    c.core_v1 = MagicMock()
    return c


def pool_ssl_context(c):
    return c.core_v1.api_client.rest_client.pool_manager.connection_pool_kw.get("ssl_context")


class TestTLS:
    def test_verification_is_on_by_default(self):
        c = KubernetesClient()
        assert c.core_v1.api_client.configuration.verify_ssl is True
        ctx = pool_ssl_context(c)
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True

    def test_strict_x509_flag_is_relaxed_but_nothing_else(self):
        ctx = pool_ssl_context(KubernetesClient())
        assert not ctx.verify_flags & ssl.VERIFY_X509_STRICT

    def test_opt_out_disables_verification_and_warns(self, caplog):
        with caplog.at_level(logging.WARNING):
            c = KubernetesClient(verify_ssl=False)
        assert c.core_v1.api_client.configuration.verify_ssl is False
        assert pool_ssl_context(c) is None
        assert "verification is disabled" in caplog.text

    def test_all_apis_share_one_api_client(self):
        c = KubernetesClient()
        assert c.batch_v1.api_client is c.core_v1.api_client is c.apps_v1.api_client

    def test_init_failure_is_wrapped(self, monkeypatch):
        def boom():
            raise RuntimeError("no cluster")
        monkeypatch.setattr(k8s_module.config, "load_incluster_config", boom)
        monkeypatch.setattr(k8s_module.config, "load_kube_config", lambda **kw: boom())
        with pytest.raises(KubernetesClientError):
            KubernetesClient()


class TestPods:
    def test_pod_exists(self, mocked_api):
        assert mocked_api.pod_exists("p", "ns") is True

    def test_pod_missing(self, mocked_api):
        mocked_api.core_v1.read_namespaced_pod.side_effect = ApiException(status=404)
        assert mocked_api.pod_exists("p", "ns") is False

    def test_pod_check_other_errors_raise(self, mocked_api):
        mocked_api.core_v1.read_namespaced_pod.side_effect = ApiException(status=500)
        with pytest.raises(KubernetesClientError):
            mocked_api.pod_exists("p", "ns")

    def test_get_pods(self, mocked_api):
        pod = MagicMock()
        pod.metadata.name = "p1"
        mocked_api.core_v1.list_namespaced_pod.return_value.items = [pod]
        assert mocked_api.get_pods("ns") == ["p1"]


class TestJobs:
    def test_create_job_submits_template(self, mocked_api):
        mocked_api.create_job("j", "alpine", ["true"], namespace="ns")
        body = mocked_api.batch_v1.create_namespaced_job.call_args.kwargs["body"]
        assert body["metadata"]["name"] == "j"

    def test_create_job_error_is_wrapped(self, mocked_api):
        mocked_api.batch_v1.create_namespaced_job.side_effect = ApiException(status=403)
        with pytest.raises(KubernetesClientError):
            mocked_api.create_job("j", "alpine", ["true"])

    @pytest.mark.parametrize("status, expected", [("succeeded", 0), ("failed", 1)])
    def test_wait_returns_exit_code_and_logs(self, mocked_api, status, expected):
        mocked_api.batch_v1.read_namespaced_job.return_value.status = MagicMock(
            succeeded=1 if status == "succeeded" else None,
            failed=1 if status == "failed" else None,
        )
        mocked_api.core_v1.list_namespaced_pod.return_value.items = [MagicMock()]
        mocked_api.core_v1.read_namespaced_pod_log.return_value = "the logs"
        assert mocked_api.wait_for_job_completion("j", "ns", timeout=5) == (expected, "the logs")

    def test_wait_times_out(self, mocked_api):
        mocked_api.batch_v1.read_namespaced_job.return_value.status = MagicMock(succeeded=None, failed=None)
        with pytest.raises(JobExecutionError, match="timed out"):
            mocked_api.wait_for_job_completion("j", "ns", timeout=0)

    def test_log_container_name_matches_template(self, mocked_api):
        mocked_api.core_v1.list_namespaced_pod.return_value.items = [MagicMock()]
        mocked_api.get_job_logs("j", "ns")
        template_container = create_job_template("j", "img", ["true"])["spec"]["template"]["spec"]["containers"][0]["name"]
        assert mocked_api.core_v1.read_namespaced_pod_log.call_args.kwargs["container"] == template_container

    def test_cleanup_job_deletes(self, mocked_api):
        mocked_api.cleanup_job("j", "ns")
        assert mocked_api.batch_v1.delete_namespaced_job.call_args.kwargs["name"] == "j"
