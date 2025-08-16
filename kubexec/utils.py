"""Utility functions for kubexec"""

import os
import random
import string
import time
from typing import Tuple


def make_unique_name(base_name: str = "kubexec") -> str:
    """Generate a unique name for pods/jobs with collision avoidance"""
    timestamp = int(time.time())
    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{base_name}-{timestamp}-{random_suffix}"


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


def validate_resource_spec(resource: str, resource_type: str) -> str:
    """Validate resource specification (memory/CPU)"""
    if resource_type == "memory":
        if not any(resource.endswith(unit) for unit in ['Mi', 'Gi', 'Ti', 'm', 'k', 'M', 'G', 'T']):
            # Add default unit if none specified
            if resource.isdigit():
                return f"{resource}Mi"
            raise ValueError(f"Invalid memory spec: {resource}")
    elif resource_type == "cpu":
        try:
            float(resource)
            return resource
        except ValueError:
            if resource.endswith('m'):
                return resource
            raise ValueError(f"Invalid CPU spec: {resource}")
    
    return resource