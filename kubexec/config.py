"""Configuration management for kubexec"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from .exceptions import ConfigurationError


class Config:
    """Configuration manager for kubexec"""
    
    DEFAULT_CONFIG_DIRS = [
        f"/shared/team/kubexec/{os.getenv('USER', 'default')}/",
        os.path.join(os.path.expanduser("~"), ".config", "kubexec"),
        f"/tmp/kubexec"
    ]
    
    DEFAULT_CONFIG_FILE = "config.yaml"
    
    DEFAULT_CONFIG = {
        'docker_image': 'ubuntu:latest',
        'namespace': None,  # Will be auto-detected from service account
        'memory': '1Gi',
        'cpu': '1',
        'workdir': '/tmp',
        'cleanup': True,
        'verbose': False,
        'timeout': 3600,  # 1 hour default timeout
        'ttl_seconds_after_finished': 60,  # Based on Nextflow config
        'security_context': {
            'fsGroup': 1000,
            'runAsUser': 1000,
            'runAsGroup': 1000,
            'fsGroupChangePolicy': 'OnRootMismatch'
        },
        'node_selector': {
            'hub.jupyter.org/node-purpose': 'user'
        },
        'automount_service_account_token': False
    }
    
    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file or self._find_config_file()
        # Detect namespace before loading config so it can be used as default
        self._detected_namespace = self._detect_namespace()
        self.config = self._load_config()
    
    def _find_config_file(self) -> str:
        """Find configuration file using fallback strategy"""
        for config_dir in self.DEFAULT_CONFIG_DIRS:
            config_path = os.path.join(config_dir, self.DEFAULT_CONFIG_FILE)
            if os.path.exists(config_path):
                return config_path
        
        # Return first directory as default location
        return os.path.join(self.DEFAULT_CONFIG_DIRS[0], self.DEFAULT_CONFIG_FILE)
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file or create default"""
        if not os.path.exists(self.config_file):
            self._create_default_config()
        
        try:
            with open(self.config_file, 'r') as f:
                config = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ConfigurationError(f"Invalid YAML in config file {self.config_file}: {e}")
        except IOError as e:
            raise ConfigurationError(f"Cannot read config file {self.config_file}: {e}")
        
        # Merge with defaults
        merged_config = self.DEFAULT_CONFIG.copy()
        merged_config.update(config)
        
        # Auto-detect namespace if not set
        if not merged_config.get('namespace'):
            merged_config['namespace'] = self._detected_namespace
        
        # Override with environment variables
        self._apply_env_overrides(merged_config)
        
        return merged_config
    
    def _create_default_config(self) -> None:
        """Create default configuration file"""
        config_dir = os.path.dirname(self.config_file)
        os.makedirs(config_dir, exist_ok=True)
        
        # Use detected namespace in default config
        default_config = self.DEFAULT_CONFIG.copy()
        default_config['namespace'] = self._detected_namespace
        
        with open(self.config_file, 'w') as f:
            yaml.dump(default_config, f, default_flow_style=False, indent=2)
    
    def _apply_env_overrides(self, config: Dict[str, Any]) -> None:
        """Apply environment variable overrides"""
        env_mappings = {
            'KUBEXEC_DOCKER_IMAGE': 'docker_image',
            'KUBEXEC_NAMESPACE': 'namespace',
            'KUBEXEC_MEMORY': 'memory',
            'KUBEXEC_CPU': 'cpu',
            'KUBEXEC_WORKDIR': 'workdir',
            'KUBEXEC_CLEANUP': 'cleanup',
            'KUBEXEC_VERBOSE': 'verbose',
            'KUBEXEC_TIMEOUT': 'timeout',
        }
        
        for env_var, config_key in env_mappings.items():
            env_value = os.getenv(env_var)
            if env_value is not None:
                if config_key in ['cleanup', 'verbose']:
                    config[config_key] = env_value.lower() in ('true', '1', 'yes', 'on')
                elif config_key == 'timeout':
                    config[config_key] = int(env_value)
                else:
                    config[config_key] = env_value
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value"""
        return self.config.get(key, default)
    
    def update(self, updates: Dict[str, Any]) -> None:
        """Update configuration values"""
        self.config.update(updates)
    
    def _detect_namespace(self) -> str:
        """Auto-detect current namespace from service account or kubectl context"""
        # Try service account namespace first (in-cluster)
        try:
            with open('/var/run/secrets/kubernetes.io/serviceaccount/namespace', 'r') as f:
                namespace = f.read().strip()
                if namespace:
                    return namespace
        except (FileNotFoundError, IOError):
            pass
        
        # Try kubectl config
        try:
            import subprocess
            result = subprocess.run(
                ['kubectl', 'config', 'view', '--minify', '--output', 'jsonpath={..namespace}'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
            pass
        
        # Default fallback
        return 'default'
    
    def save(self) -> None:
        """Save current configuration to file"""
        with open(self.config_file, 'w') as f:
            yaml.dump(self.config, f, default_flow_style=False, indent=2)