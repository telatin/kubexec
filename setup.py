"""Setup script for kubexec package"""

from setuptools import setup, find_packages

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
    version="0.6.1",
    author="kubexec team",
    description="Execute commands and scripts on Kubernetes pods with Docker access",
    long_description=long_description,
    long_description_content_type="text/markdown",
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
    entry_points={
        "console_scripts": [
            "kubexec=kubexec.cli:main",
            "kuberlist=kubexec.kuberlist:main",
        ],
    },
)