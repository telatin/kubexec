"""Custom exceptions for kubexec"""


class KubeExecError(Exception):
    """Base exception for kubexec"""
    pass


class KubernetesClientError(KubeExecError):
    """Kubernetes client operation failed"""
    pass


class JobExecutionError(KubeExecError):
    """Job execution failed"""
    pass


class ConfigurationError(KubeExecError):
    """Configuration error"""
    pass


class PodNotFoundError(KubeExecError):
    """Pod not found"""
    pass