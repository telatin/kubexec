import re

import pytest

from kubexec.utils import (
    is_script_file,
    make_unique_name,
    parse_volume_mount,
    validate_resource_spec,
)


def test_make_unique_name_format_and_uniqueness():
    names = {make_unique_name("kubexec-job") for _ in range(50)}
    assert len(names) == 50
    assert all(re.fullmatch(r"kubexec-job-\d+-[a-z0-9]{6}", n) for n in names)


def test_is_script_file(tmp_path):
    script = tmp_path / "run.sh"
    script.write_text("echo hi")
    plain = tmp_path / "notes.txt"
    plain.write_text("hello")
    executable = tmp_path / "tool"
    executable.write_text("#!/bin/sh")
    executable.chmod(0o755)

    assert is_script_file(str(script))
    assert is_script_file(str(executable))
    assert not is_script_file(str(plain))
    assert not is_script_file(str(tmp_path / "missing.sh"))
    assert not is_script_file("echo hello")


def test_parse_volume_mount():
    assert parse_volume_mount("/a:/b") == ("/a", "/b", False)
    assert parse_volume_mount("/a:/b:ro") == ("/a", "/b", True)
    with pytest.raises(ValueError):
        parse_volume_mount("/only-one-part")


@pytest.mark.parametrize("spec, expected", [
    ("1Gi", "1Gi"), ("512Mi", "512Mi"), ("512Ki", "512Ki"), ("1.5Gi", "1.5Gi"),
    ("2G", "2G"), ("100M", "100M"), ("1Ti", "1Ti"),
    ("512", "512Mi"),  # bare number means MiB
    (" 2Gi ", "2Gi"), (4, "4Mi"),
])
def test_validate_memory(spec, expected):
    assert validate_resource_spec(spec, "memory") == expected


@pytest.mark.parametrize("spec", ["lots", "512m", "abcGi", "Gi", "-1Gi", "1 Gi", "1Gb", ""])
def test_validate_memory_rejects_garbage(spec):
    with pytest.raises(ValueError, match="Invalid memory spec"):
        validate_resource_spec(spec, "memory")


@pytest.mark.parametrize("spec", ["1", "0.5", "500m", "16"])
def test_validate_cpu(spec):
    assert validate_resource_spec(spec, "cpu") == spec


@pytest.mark.parametrize("spec", ["fast", "abcm", "m", "-1", "1.5m", "1Gi", ""])
def test_validate_cpu_rejects_garbage(spec):
    with pytest.raises(ValueError, match="Invalid CPU spec"):
        validate_resource_spec(spec, "cpu")
