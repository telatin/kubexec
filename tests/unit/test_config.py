import os

import pytest

from kubexec.config import Config
from kubexec.exceptions import ConfigurationError


def test_defaults_and_detected_namespace(config):
    assert config.get("docker_image") == "ubuntu:latest"
    assert config.get("namespace") == "test-ns"
    assert config.get("cleanup") is True
    assert config.get("verify_ssl") is True
    assert config.get("missing", "fallback") == "fallback"


def test_default_config_file_is_created(config):
    assert os.path.exists(config.config_file)


def test_file_overrides_defaults(make_config):
    config = make_config("docker_image: alpine\nnamespace: custom\nverify_ssl: false\n")
    assert config.get("docker_image") == "alpine"
    assert config.get("namespace") == "custom"
    assert config.get("verify_ssl") is False
    assert config.get("memory") == "1Gi"


def test_env_overrides_file(make_config, monkeypatch):
    monkeypatch.setenv("KUBEXEC_DOCKER_IMAGE", "python:3.12")
    monkeypatch.setenv("KUBEXEC_CLEANUP", "no")
    monkeypatch.setenv("KUBEXEC_VERIFY_SSL", "false")
    monkeypatch.setenv("KUBEXEC_TIMEOUT", "42")
    config = make_config("docker_image: alpine\n")
    assert config.get("docker_image") == "python:3.12"
    assert config.get("cleanup") is False
    assert config.get("verify_ssl") is False
    assert config.get("timeout") == 42


def test_invalid_yaml_raises(make_config):
    with pytest.raises(ConfigurationError):
        make_config("key: [unclosed")


def test_update_and_save_roundtrip(make_config):
    config = make_config()
    config.update({"memory": "4Gi"})
    config.save()
    assert Config(config.config_file).get("memory") == "4Gi"


@pytest.mark.parametrize("value", ["abc", "", "1.5", "0", "-5"])
def test_invalid_timeout_env_raises_configuration_error(make_config, monkeypatch, value):
    monkeypatch.setenv("KUBEXEC_TIMEOUT", value)
    with pytest.raises(ConfigurationError, match="KUBEXEC_TIMEOUT"):
        make_config()


@pytest.mark.parametrize("yaml_value", ["abc", "0", "-5", "1.5", "true", "[1]", "null"])
def test_invalid_timeout_in_file_raises_configuration_error(make_config, yaml_value):
    with pytest.raises(ConfigurationError, match="'timeout'"):
        make_config(f"timeout: {yaml_value}\n")


@pytest.mark.parametrize("yaml_value, expected", [("120", 120), ("'300'", 300)])
def test_valid_timeout_in_file(make_config, yaml_value, expected):
    assert make_config(f"timeout: {yaml_value}\n").get("timeout") == expected


def test_env_timeout_overrides_invalid_file_value(make_config, monkeypatch):
    monkeypatch.setenv("KUBEXEC_TIMEOUT", "60")
    assert make_config("timeout: abc\n").get("timeout") == 60
