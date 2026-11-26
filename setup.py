"""Setup script for kubexec package"""

import re
from pathlib import Path
from setuptools import setup, find_packages

# Single source of truth: kubexec/__init__.py (read, not imported, since the
# package dependencies may not be installed yet when this runs)
version = re.search(
    r'^__version__\s*=\s*["\']([^"\']+)["\']',
    (Path(__file__).parent / "kubexec" / "__init__.py").read_text(encoding="utf-8"),
    re.M,
).group(1)

try:
    with open("README.md", "r", encoding="utf-8") as fh:
        long_description = fh.read()
except FileNotFoundError:
    long_description = "Execute commands and scripts on Kubernetes pods with Docker access"

try:
    with open("requirements.txt", "r", encoding="utf-8") as fh:
        requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]
except FileNotFoundError:
    requirements = [
        "kubernetes>=28.1.0",
        "PyYAML>=6.0",
        "rich>=13.0.0"
    ]

setup(
    name="kubexec",
    version=version,
    author="kubexec team",
    license="MIT",
    license_files=["LICENSE"],
    description="Execute commands and scripts on Kubernetes pods with Docker access",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/telatin/kubexec",
    project_urls={
        "Source": "https://github.com/telatin/kubexec",
        "Issues": "https://github.com/telatin/kubexec/issues",
        "Documentation": "https://github.com/telatin/kubexec#readme",
    },
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    ],
    python_requires=">=3.10",
    install_requires=requirements,
    extras_require={"test": ["pytest>=7"]},
    entry_points={
        "console_scripts": [
            "kubexec=kubexec.cli:main",
            "kuberlist=kubexec.kuberlist:main",
        ],
    },
)