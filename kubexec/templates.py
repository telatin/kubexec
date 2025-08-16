"""Kubernetes YAML templates for kubexec"""

from typing import Dict, List, Any, Optional


def create_job_template(
    name: str,
    image: str,
    command: List[str],
    namespace: str = "default",
    memory: str = "1Gi",
    cpu: str = "1",
    workdir: str = "/tmp",
    volume_mounts: Optional[List[Dict[str, Any]]] = None,
    volumes: Optional[List[Dict[str, Any]]] = None,
    security_context: Optional[Dict[str, Any]] = None,
    env_vars: Optional[List[Dict[str, str]]] = None,
    restart_policy: str = "Never",
    backoff_limit: int = 0,
    ttl_seconds_after_finished: int = 60,
    node_selector: Optional[Dict[str, str]] = None,
    automount_service_account_token: bool = False
) -> Dict[str, Any]:
    """Create a Kubernetes Job template"""
    
    if security_context is None:
        security_context = {
            'fsGroup': 1000,
            'runAsUser': 1000,
            'runAsGroup': 1000,
            'fsGroupChangePolicy': 'OnRootMismatch'
        }
    
    container_spec = {
        'name': 'kubexec-container',
        'image': image,
        'command': command,
        'workingDir': workdir,
        'resources': {
            'limits': {
                'memory': memory,
                'cpu': cpu
            },
            'requests': {
                'memory': memory,
                'cpu': cpu
            }
        }
    }
    
    if volume_mounts:
        container_spec['volumeMounts'] = volume_mounts
    
    if env_vars:
        container_spec['env'] = env_vars
    
    pod_spec = {
        'restartPolicy': restart_policy,
        'securityContext': security_context,
        'automountServiceAccountToken': automount_service_account_token,
        'containers': [container_spec]
    }
    
    if node_selector:
        pod_spec['nodeSelector'] = node_selector
    
    if volumes:
        pod_spec['volumes'] = volumes
    
    job_template = {
        'apiVersion': 'batch/v1',
        'kind': 'Job',
        'metadata': {
            'name': name,
            'namespace': namespace,
            'labels': {
                'app': 'kubexec',
                'created-by': 'kubexec'
            }
        },
        'spec': {
            'backoffLimit': backoff_limit,
            'ttlSecondsAfterFinished': ttl_seconds_after_finished,
            'template': {
                'metadata': {
                    'labels': {
                        'app': 'kubexec',
                        'job': name
                    }
                },
                'spec': pod_spec
            }
        }
    }
    
    return job_template


def create_configmap_template(
    name: str,
    namespace: str,
    script_content: str,
    script_name: str = "script.sh"
) -> Dict[str, Any]:
    """Create a ConfigMap template for script storage"""
    
    return {
        'apiVersion': 'v1',
        'kind': 'ConfigMap',
        'metadata': {
            'name': name,
            'namespace': namespace,
            'labels': {
                'app': 'kubexec',
                'created-by': 'kubexec'
            }
        },
        'data': {
            script_name: script_content
        }
    }


def create_shared_volumes() -> List[Dict[str, Any]]:
    """Create shared volume configurations based on cluster setup"""
    return [
        {
            'name': 'shared-team-volume',
            'persistentVolumeClaim': {
                'claimName': 'cephfs-shared-team'
            }
        },
        {
            'name': 'shared-public-volume',
            'persistentVolumeClaim': {
                'claimName': 'cephfs-shared-ro-public'
            }
        }
    ]


def create_shared_volume_mounts() -> List[Dict[str, Any]]:
    """Create shared volume mount configurations"""
    return [
        {
            'name': 'shared-team-volume',
            'mountPath': '/shared/team'
        },
        {
            'name': 'shared-public-volume',
            'mountPath': '/shared/public',
            'readOnly': True
        }
    ]