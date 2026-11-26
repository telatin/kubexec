import re
import runpy
import sys
from pathlib import Path
from types import ModuleType

import kubexec

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_version_looks_like_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", kubexec.__version__)


def test_setup_py_uses_package_version(monkeypatch):
    """setup.py must derive its version from kubexec/__init__.py"""
    captured = {}
    fake_setuptools = ModuleType("setuptools")
    fake_setuptools.setup = lambda **kwargs: captured.update(kwargs)
    fake_setuptools.find_packages = lambda *a, **kw: []
    monkeypatch.setitem(sys.modules, "setuptools", fake_setuptools)
    monkeypatch.chdir(REPO_ROOT)

    runpy.run_path(str(REPO_ROOT / "setup.py"))

    assert captured["version"] == kubexec.__version__
