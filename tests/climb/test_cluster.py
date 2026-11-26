"""Read-only checks against the live CLIMB cluster"""

import sys
import warnings

import certifi
import pytest
import urllib3
from kubernetes import client
from kubernetes.client.rest import ApiException

from kubexec import cli, kuberlist
from kubexec.config import Config
from kubexec.exceptions import KubernetesClientError
from kubexec.k8s_client import KubernetesClient
from kubexec.templates import create_shared_volumes

from tests.conftest import SERVICE_ACCOUNT_DIR


def test_namespace_autodetected_from_service_account(namespace):
    assert namespace == (SERVICE_ACCOUNT_DIR / "namespace").read_text().strip()


def test_api_reachable_with_tls_verification_and_no_warnings(k8s, namespace):
    assert k8s.core_v1.api_client.configuration.verify_ssl is True
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        pods = k8s.get_pods(namespace)
    assert isinstance(pods, list)


def test_untrusted_ca_is_rejected(monkeypatch, namespace):
    """Verification must really be on: a CA that is not the cluster's is refused"""
    original = client.Configuration.get_default_copy

    def with_wrong_ca():
        configuration = original()
        configuration.ssl_ca_cert = certifi.where()
        return configuration

    monkeypatch.setattr(client.Configuration, "get_default_copy", with_wrong_ca)
    # Connection-level errors currently surface as raw urllib3 errors, not KubernetesClientError
    with pytest.raises((KubernetesClientError, urllib3.exceptions.HTTPError), match="CERTIFICATE_VERIFY_FAILED"):
        KubernetesClient().get_pods(namespace)


def test_opt_out_still_works(namespace):
    assert isinstance(KubernetesClient(verify_ssl=False).get_pods(namespace), list)


def test_pod_exists_for_missing_pod(k8s, namespace):
    assert k8s.pod_exists("kubexec-test-no-such-pod", namespace) is False


def test_shared_volume_claims_exist(k8s, namespace):
    for volume in create_shared_volumes():
        claim = volume["persistentVolumeClaim"]["claimName"]
        try:
            k8s.core_v1.read_namespaced_persistent_volume_claim(claim, namespace)
        except ApiException as e:
            if e.status == 403:
                pytest.skip("not allowed to read PVCs in this namespace")
            raise


def test_list_jobs_via_executor(k8s, namespace, real_config):
    from kubexec.executor import KubeExecutor
    jobs = KubeExecutor(real_config, k8s).list_jobs(namespace)
    assert isinstance(jobs, list)


def test_kubexec_list_jobs_cli(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["kubexec", "--config", str(tmp_path / "c.yaml"), "--list-jobs"])
    assert cli.main() == 0


def test_kuberlist_cli(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["kuberlist"])
    assert kuberlist.main() == 0
