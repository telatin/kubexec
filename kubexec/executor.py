"""Command and script execution logic for kubexec"""

import os
import logging
from typing import List, Optional, Dict, Any, Tuple
from .k8s_client import KubernetesClient
from .config import Config
from .utils import make_unique_name, is_script_file, parse_volume_mount, validate_resource_spec
from .exceptions import KubeExecError, JobExecutionError


logger = logging.getLogger(__name__)


class KubeExecutor:
    """Main executor for kubexec operations"""
    
    def __init__(self, config: Config, k8s_client: Optional[KubernetesClient] = None):
        self.config = config
        self.k8s_client = k8s_client or KubernetesClient()
    
    def execute(
        self,
        target: str,
        docker_image: Optional[str] = None,
        namespace: Optional[str] = None,
        pod_name: Optional[str] = None,
        memory: Optional[str] = None,
        cpu: Optional[str] = None,
        workdir: Optional[str] = None,
        volume_mounts: Optional[List[str]] = None,
        create_pod: bool = False,
        cleanup: Optional[bool] = None,
        dry_run: bool = False,
        **kwargs
    ) -> Tuple[int, str]:
        """Execute target command or script"""
        
        # Use config defaults, override with parameters
        image = docker_image or self.config.get('docker_image')
        ns = namespace or self.config.get('namespace')
        mem = validate_resource_spec(memory or self.config.get('memory'), 'memory')
        cpu_limit = validate_resource_spec(cpu or self.config.get('cpu'), 'cpu')
        work_dir = workdir or self.config.get('workdir')
        should_cleanup = cleanup if cleanup is not None else self.config.get('cleanup')
        
        # Determine execution strategy
        if pod_name and not create_pod and self.k8s_client.pod_exists(pod_name, ns):
            return self._execute_in_existing_pod(target, pod_name, ns, dry_run)
        else:
            return self._execute_in_new_job(
                target, image, ns, mem, cpu_limit, work_dir, 
                volume_mounts, should_cleanup, dry_run, **kwargs
            )
    
    def _execute_in_existing_pod(
        self,
        target: str,
        pod_name: str,
        namespace: str,
        dry_run: bool
    ) -> Tuple[int, str]:
        """Execute command in existing pod"""
        
        # Note: For existing pods, we can't mount volumes, so we work in the current directory
        if is_script_file(target):
            # For script files, we need to copy the script to the pod first
            script_content = self._read_script_file(target)
            script_name = os.path.basename(target)
            command = ["/bin/bash", "-c", f"cat << 'EOF' > /tmp/{script_name}\n{script_content}\nEOF\nchmod +x /tmp/{script_name} && /tmp/{script_name}"]
        else:
            # Direct command execution in existing pod
            command = ["/bin/bash", "-c", target]
        
        if dry_run:
            return 0, f"Would execute in pod {pod_name}: {' '.join(command)}"
        
        logger.info(f"Executing in existing pod: {pod_name}")
        return self.k8s_client.execute_in_existing_pod(pod_name, command, namespace)
    
    def _execute_in_new_job(
        self,
        target: str,
        image: str,
        namespace: str,
        memory: str,
        cpu: str,
        workdir: str,
        volume_mounts: Optional[List[str]],
        cleanup: bool,
        dry_run: bool,
        **kwargs
    ) -> Tuple[int, str]:
        """Execute command in new Kubernetes job"""
        
        job_name = make_unique_name("kubexec-job")
        
        # Prepare command and volumes
        command, volumes, volume_mount_specs = self._prepare_execution(target, job_name, namespace, volume_mounts)
        
        if dry_run:
            return 0, f"Would create job {job_name} with image {image}: {' '.join(command)}"
        
        try:
            # Create job
            logger.info(f"Creating job: {job_name}")
            self.k8s_client.create_job(
                name=job_name,
                image=image,
                command=command,
                namespace=namespace,
                memory=memory,
                cpu=cpu,
                workdir=workdir,
                volumes=volumes,
                volume_mounts=volume_mount_specs,
                security_context=self.config.get('security_context'),
                ttl_seconds_after_finished=self.config.get('ttl_seconds_after_finished', 60),
                node_selector=self.config.get('node_selector'),
                automount_service_account_token=self.config.get('automount_service_account_token', False),
                **kwargs
            )
            
            # Wait for completion
            logger.info(f"Waiting for job completion: {job_name}")
            exit_code, logs = self.k8s_client.wait_for_job_completion(
                job_name, namespace, timeout=self.config.get('timeout', 3600)
            )
            
            return exit_code, logs
            
        except Exception as e:
            logger.error(f"Job execution failed: {e}")
            raise JobExecutionError(f"Job execution failed: {e}")
        
        finally:
            if cleanup:
                try:
                    logger.info(f"Cleaning up job: {job_name}")
                    self.k8s_client.cleanup_job(job_name, namespace)
                except Exception as e:
                    logger.warning(f"Failed to cleanup job {job_name}: {e}")
    
    def _prepare_execution(
        self,
        target: str,
        job_name: str,
        namespace: str,
        volume_mounts: Optional[List[str]]
    ) -> Tuple[List[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Prepare command, volumes, and volume mounts for execution"""
        
        volumes = []
        volume_mount_specs = []
        
        # Add shared volumes by default
        from .templates import create_shared_volumes, create_shared_volume_mounts
        volumes.extend(create_shared_volumes())
        volume_mount_specs.extend(create_shared_volume_mounts())
        
        # Determine kubexec entry path within shared volumes
        import os
        current_dir = os.getcwd()
        
        # Map current directory to container path using shared volumes
        if current_dir.startswith('/shared/team'):
            # Already in shared team directory
            kubexec_entry_path = current_dir.replace('/shared/team', '/shared/team')
        elif current_dir.startswith('/shared/public'):
            # In shared public directory
            kubexec_entry_path = current_dir.replace('/shared/public', '/shared/public')
        else:
            # Default to shared team directory
            kubexec_entry_path = '/shared/team'
        
        # Add custom volume mounts
        if volume_mounts:
            for vm_spec in volume_mounts:
                host_path, pod_path, readonly = parse_volume_mount(vm_spec)
                
                # Create hostPath volume
                volume_name = f"custom-volume-{len(volumes)}"
                volumes.append({
                    'name': volume_name,
                    'hostPath': {'path': host_path}
                })
                
                volume_mount_specs.append({
                    'name': volume_name,
                    'mountPath': pod_path,
                    'readOnly': readonly
                })
        
        # Prepare command
        if is_script_file(target):
            script_content = self._read_script_file(target)
            script_name = os.path.basename(target)
            
            # Embed script content directly in command instead of using ConfigMap
            # This avoids permission issues with ConfigMap creation
            command = [
                "/bin/bash", "-c",
                f"cd {kubexec_entry_path} && cat << 'KUBEXEC_SCRIPT_EOF' > /tmp/{script_name}\n{script_content}\nKUBEXEC_SCRIPT_EOF\nchmod +x /tmp/{script_name} && /tmp/{script_name}"
            ]
        else:
            # Direct command execution - change to entry directory first
            command = ["/bin/bash", "-c", f"cd {kubexec_entry_path} && {target}"]
        
        return command, volumes, volume_mount_specs
    
    def _read_script_file(self, file_path: str) -> str:
        """Read script file content"""
        try:
            with open(file_path, 'r') as f:
                return f.read()
        except IOError as e:
            raise KubeExecError(f"Failed to read script file {file_path}: {e}")
    
    def list_jobs(self, namespace: str = "default") -> List[Dict[str, Any]]:
        """List kubexec jobs in namespace"""
        try:
            jobs = self.batch_v1.list_namespaced_job(
                namespace=namespace,
                label_selector="app=kubexec"
            )
            
            job_list = []
            for job in jobs.items:
                job_info = {
                    'name': job.metadata.name,
                    'status': 'completed' if job.status.succeeded else 'failed' if job.status.failed else 'running',
                    'created': job.metadata.creation_timestamp,
                    'image': job.spec.template.spec.containers[0].image if job.spec.template.spec.containers else 'unknown'
                }
                job_list.append(job_info)
            
            return job_list
            
        except ApiException as e:
            raise KubernetesClientError(f"Failed to list jobs: {e}")
    
    def cleanup_old_jobs(self, namespace: str = "default", max_age_hours: int = 24) -> int:
        """Cleanup old kubexec jobs"""
        try:
            import datetime
            cutoff_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=max_age_hours)
            
            jobs = self.batch_v1.list_namespaced_job(
                namespace=namespace,
                label_selector="app=kubexec"
            )
            
            cleaned_count = 0
            for job in jobs.items:
                if job.metadata.creation_timestamp < cutoff_time:
                    self.k8s_client.cleanup_job(job.metadata.name, namespace)
                    cleaned_count += 1
            
            return cleaned_count
            
        except ApiException as e:
            raise KubernetesClientError(f"Failed to cleanup old jobs: {e}")