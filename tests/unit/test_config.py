import os

import pytest

from kubexec.config import Config

ORIGINAL_FIND_CONFIG_FILE = Config._find_config_file
SHIPPED_DEFAULT_DIRS = list(Config.DEFAULT_CONFIG_DIRS)
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


class TestDefaultLocation:
    @pytest.fixture(autouse=True)
    def real_lookup(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Config, "_find_config_file", ORIGINAL_FIND_CONFIG_FILE)
        monkeypatch.setattr(Config, "_detect_namespace", lambda self: "test-ns")
        self.home_config = tmp_path / "home" / ".config" / "kubexec"
        monkeypatch.setattr(Config, "DEFAULT_CONFIG_DIRS", [str(self.home_config)])

    def test_shipped_default_dir_is_in_the_home_directory(self):
        assert SHIPPED_DEFAULT_DIRS == [os.path.join(os.path.expanduser("~"), ".config", "kubexec")]

    def test_default_config_is_created_in_home_when_missing(self):
        config = Config()
        assert config.config_file == str(self.home_config / "config.yaml")
        assert os.path.exists(config.config_file)
        assert config.get("namespace") == "test-ns"

    def test_existing_default_config_is_used(self):
        self.home_config.mkdir(parents=True)
        (self.home_config / "config.yaml").write_text("docker_image: alpine\n")
        assert Config().get("docker_image") == "alpine"

    def test_unwritable_location_falls_back_to_builtin_defaults(self, monkeypatch, tmp_path):
        blocker = tmp_path / "a-file"
        blocker.write_text("")
        monkeypatch.setattr(Config, "DEFAULT_CONFIG_DIRS", [str(blocker / "kubexec")])
        config = Config()
        assert config.get("docker_image") == "ubuntu:latest"
        assert config.get("namespace") == "test-ns"
        assert not os.path.exists(config.config_file)


class TestDefaultWorkdir:
    def test_default_is_a_new_folder_per_run_for_each_user(self, config):
        assert config.get("default_workdir") == "/shared/team/kubexec/work/{user}/{run}"

    def test_file_and_env_override(self, make_config, monkeypatch):
        assert make_config("default_workdir: /shared/team/mine\n").get("default_workdir") == "/shared/team/mine"
        monkeypatch.setenv("KUBEXEC_DEFAULT_WORKDIR", "/shared/team/from-env/{user}")
        assert make_config("default_workdir: /shared/team/mine\n").get("default_workdir") == "/shared/team/from-env/{user}"

    @pytest.mark.parametrize("yaml_value", ["relative/path", "''", "null", "[1]"])
    def test_must_be_an_absolute_path(self, make_config, yaml_value):
        with pytest.raises(ConfigurationError, match="default_workdir"):
            make_config(f"default_workdir: {yaml_value}\n")
