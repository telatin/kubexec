"""Kubernetes client operations for kubexec"""

import time
import logging
from typing import List, Tuple, Optional, Dict, Any
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from .exceptions import KubernetesClientError, JobExecutionError, PodNotFoundError
from .templates import create_job_template, create_configmap_template


logger = logging.getLogger(__name__)


class KubernetesClient:
    """Kubernetes client wrapper for kubexec operations"""
    
    def __init__(self, context: Optional[str] = None):
        """Initialize Kubernetes client"""
        try:
            # Disable SSL verification warnings for self-signed certificates
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            if context:
                config.load_kube_config(context=context)
            else:
                # Try in-cluster config first, then kube config
                try:
                    config.load_incluster_config()
                except config.ConfigException:
                    try:
                        config.load_kube_config()
                    except config.ConfigException:
                        # If no kubeconfig exists, try to use service account
                        logger.warning("No kubeconfig found, using service account authentication")
                        config.load_incluster_config()
            
            # Configure client for potential SSL issues
            configuration = client.Configuration.get_default_copy()
            if hasattr(configuration, 'verify_ssl'):
                configuration.verify_ssl = False
            
            self.batch_v1 = client.BatchV1Api(client.ApiClient(configuration))
            self.core_v1 = client.CoreV1Api(client.ApiClient(configuration))
            self.apps_v1 = client.AppsV1Api(client.ApiClient(configuration))
            
        except Exception as e:
            raise KubernetesClientError(f"Failed to initialize Kubernetes client: {e}")
    
    def get_pods(self, namespace: str = "default") -> List[str]:
        """Get list of pod names in namespace"""
        try:
            pods = self.core_v1.list_namespaced_pod(namespace=namespace)
            return [pod.metadata.name for pod in pods.items]
        except ApiException as e:
            raise KubernetesClientError(f"Failed to list pods: {e}")
    
    def pod_exists(self, pod_name: str, namespace: str = "default") -> bool:
        """Check if pod exists"""
        try:
            self.core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            raise KubernetesClientError(f"Failed to check pod existence: {e}")
    
    def create_configmap(self, name: str, namespace: str, script_content: str, script_name: str = "script.sh") -> str:
        """Create ConfigMap with script content"""
        try:
            configmap_template = create_configmap_template(name, namespace, script_content, script_name)
            self.core_v1.create_namespaced_config_map(
                namespace=namespace,
                body=configmap_template
            )
            return name
        except ApiException as e:
            raise KubernetesClientError(f"Failed to create ConfigMap: {e}")
    
    def create_job(
        self,
        name: str,
        image: str,
        command: List[str],
        namespace: str = "default",
        **kwargs
    ) -> str:
        """Create Kubernetes Job"""
        try:
            job_template = create_job_template(
                name=name,
                image=image,
                command=command,
                namespace=namespace,
                **kwargs
            )
            
            self.batch_v1.create_namespaced_job(
                namespace=namespace,
                body=job_template
            )
            
            logger.info(f"Created job: {name}")
            return name
            
        except ApiException as e:
            raise KubernetesClientError(f"Failed to create job: {e}")
    
    def wait_for_job_completion(self, job_name: str, namespace: str = "default", timeout: int = 3600) -> Tuple[int, str]:
        """Wait for job completion and return exit code and logs"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                job = self.batch_v1.read_namespaced_job(name=job_name, namespace=namespace)
                
                if job.status.succeeded:
                    logs = self.get_job_logs(job_name, namespace)
                    return 0, logs
                elif job.status.failed:
                    logs = self.get_job_logs(job_name, namespace)
                    return 1, logs
                
                time.sleep(2)
                
            except ApiException as e:
                raise KubernetesClientError(f"Failed to check job status: {e}")
        
        raise JobExecutionError(f"Job {job_name} timed out after {timeout} seconds")
    
    def get_job_logs(self, job_name: str, namespace: str = "default") -> str:
        """Get logs from job's pod"""
        try:
            # Find pod created by the job
            pods = self.core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"job-name={job_name}"
            )
            
            if not pods.items:
                return "No pod found for job"
            
            pod_name = pods.items[0].metadata.name
            logs = self.core_v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container="kubexec-container"
            )
            return logs
            
        except ApiException as e:
            logger.warning(f"Failed to get job logs: {e}")
            return f"Failed to retrieve logs: {e}"
    
    def cleanup_job(self, job_name: str, namespace: str = "default") -> None:
        """Delete job and associated resources"""
        try:
            # Delete job (this will also delete associated pods)
            self.batch_v1.delete_namespaced_job(
                name=job_name,
                namespace=namespace,
                body=client.V1DeleteOptions(
                    propagation_policy="Foreground"
                )
            )
            
            # Delete ConfigMap if it exists
            try:
                self.core_v1.delete_namespaced_config_map(
                    name=f"{job_name}-script",
                    namespace=namespace
                )
            except ApiException:
                pass  # ConfigMap might not exist
            
            logger.info(f"Cleaned up job: {job_name}")
            
        except ApiException as e:
            raise KubernetesClientError(f"Failed to cleanup job: {e}")
    
    def execute_in_existing_pod(
        self,
        pod_name: str,
        command: List[str],
        namespace: str = "default",
        container: Optional[str] = None
    ) -> Tuple[int, str]:
        """Execute command in existing pod"""
        try:
            if not self.pod_exists(pod_name, namespace):
                raise PodNotFoundError(f"Pod {pod_name} not found in namespace {namespace}")
            
            # Use kubectl exec through subprocess as kubernetes client doesn't support exec well
            import subprocess
            
            kubectl_cmd = ["kubectl", "exec", "-n", namespace, pod_name]
            if container:
                kubectl_cmd.extend(["-c", container])
            kubectl_cmd.extend(["--"] + command)
            
            result = subprocess.run(
                kubectl_cmd,
                capture_output=True,
                text=True,
                timeout=3600
            )
            
            return result.returncode, result.stdout + result.stderr
            
        except subprocess.TimeoutExpired:
            raise JobExecutionError("Command execution timed out")
        except Exception as e:
            raise KubernetesClientError(f"Failed to execute in pod: {e}")