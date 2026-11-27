"""Utility functions for kubexec"""

import getpass
import os
import random
import re
import string
import time
from typing import Tuple


def make_unique_name(base_name: str = "kubexec") -> str:
    """Generate a unique name for pods/jobs with collision avoidance"""
    timestamp = int(time.time())
    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{base_name}-{timestamp}-{random_suffix}"


def get_user_identity() -> str:
    """Name identifying the current user, usable as a Kubernetes label value

    On CLIMB every user is uid 1000 ("jovyan") and $USER is unset, so the
    JupyterHub login name is the only reliable identity.
    """
    name = os.getenv("JUPYTERHUB_USER") or os.getenv("USER")
    if not name:
        try:
            name = getpass.getuser()
        except Exception:
            name = ""
    return sanitize_label_value(name)


def sanitize_label_value(value: str) -> str:
    """Make a string valid as a Kubernetes label value (<=63 chars, [A-Za-z0-9_.-], alphanumeric ends)"""
    cleaned = re.sub(r'[^A-Za-z0-9_.-]', '_', value or "")[:63].strip('_.-')
    return cleaned or "unknown"


def make_unique_filename(filename: str, ext: str = "") -> str:
    """Generate a unique filename with collision avoidance"""
    base = os.path.splitext(filename)[0]
    if not ext:
        ext = os.path.splitext(filename)[1]
    
    timestamp = int(time.time())
    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{base}-{timestamp}-{random_suffix}{ext}"


def is_script_file(target: str) -> bool:
    """Detect if target is a script file vs command string"""
    return (
        os.path.exists(target) and 
        (target.endswith(('.sh', '.py', '.pl', '.R', '.rb', '.js')) or 
         os.access(target, os.X_OK))
    )


def parse_volume_mount(volume_spec: str) -> Tuple[str, str, bool]:
    """Parse volume mount specification: host_path:pod_path[:ro]"""
    parts = volume_spec.split(':')
    if len(parts) < 2:
        raise ValueError(f"Invalid volume spec: {volume_spec}. Expected format: host_path:pod_path[:ro]")
    
    host_path = parts[0]
    pod_path = parts[1]
    readonly = len(parts) > 2 and parts[2] == "ro"
    
    return host_path, pod_path, readonly


_MEMORY_RE = re.compile(r'\d+(\.\d+)?(Ki|Mi|Gi|Ti|Pi|Ei|k|M|G|T|P|E)')
_CPU_RE = re.compile(r'(\d+(\.\d+)?|\d+m)')


def validate_resource_spec(resource: str, resource_type: str) -> str:
    """Validate resource specification (memory/CPU)

    Memory uses Kubernetes quantity suffixes (Mi, Gi, M, G, ...); a bare number
    is taken as MiB. Note that 'm' (milli) is only valid for CPU.
    """
    resource = str(resource).strip()
    if resource_type == "memory":
        if resource.isdigit():
            return f"{resource}Mi"
        if not _MEMORY_RE.fullmatch(resource):
            raise ValueError(f"Invalid memory spec: {resource!r}. Use a number with a unit such as 512Mi or 2Gi")
    elif resource_type == "cpu":
        if not _CPU_RE.fullmatch(resource):
            raise ValueError(f"Invalid CPU spec: {resource!r}. Use cores (e.g. 2, 0.5) or millicores (e.g. 500m)")
    
    return resource