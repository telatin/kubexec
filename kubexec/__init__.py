"""
kubexec - Execute commands and scripts on Kubernetes pods with Docker access
"""

__version__ = "0.6.1"
__author__ = "kubexec team"

from .cli import main
from .executor import KubeExecutor
from .config import Config

__all__ = ["main", "KubeExecutor", "Config"]