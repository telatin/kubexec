"""Command-line interface for kubexec"""

import argparse
import logging
import sys
import os
from typing import List, Optional
from .config import Config
from .executor import KubeExecutor
from .k8s_client import KubernetesClient
from .exceptions import KubeExecError


def setup_logging(verbose: bool = False) -> None:
    """Setup logging configuration"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s' if verbose else '%(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def create_parser() -> argparse.ArgumentParser:
    """Create command-line argument parser"""
    parser = argparse.ArgumentParser(
        prog='kubexec',
        description='Execute commands or scripts on Kubernetes pods with Docker access',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  kubexec 'echo "Hello, Kubernetes!"'
  kubexec -d python:3.9 'python -c "import sys; print(sys.version)"'
  kubexec -m 4Gi -c 2 ./my_script.sh
  kubexec -p existing-pod 'ls -la'
  kubexec --dry-run -d alpine 'apk add --no-cache curl'
        """
    )
    
    parser.add_argument(
        'target',
        nargs='?',
        metavar='TARGET',
        help='Command string or path to script file to execute'
    )
    
    parser.add_argument(
        '-d', '--docker-image',
        help='Docker image for new pods (default: ubuntu:latest)'
    )
    
    parser.add_argument(
        '-n', '--namespace',
        help='Kubernetes namespace (default: default)'
    )
    
    parser.add_argument(
        '-p', '--pod-name',
        help='Target existing pod name or name for new pod'
    )
    
    parser.add_argument(
        '-m', '--memory',
        help='Memory limit for new pods (default: 1Gi)'
    )
    
    parser.add_argument(
        '-c', '--cpu',
        help='CPU limit for new pods (default: 1)'
    )
    
    parser.add_argument(
        '-w', '--workdir',
        help=('Directory to run in inside the job. Default: the current directory if it is under '
              '/shared/team or /shared/public, otherwise a new folder per run '
              '(config key default_workdir, /shared/team/kubexec/work/{user}/{run}). '
              'Relative paths are resolved against that default. {user} is your login name '
              'and {run} a unique id per run, usable in paths')
    )
    
    parser.add_argument(
        '-v', '--volume',
        action='append',
        dest='volume_mounts',
        help='Volume mount (format: host_path:pod_path[:ro]). Can be used multiple times.'
    )
    
    parser.add_argument(
        '--config',
        help='Configuration file (default: ~/.config/kubexec/config.yaml)'
    )
    
    parser.add_argument(
        '--context',
        help='Kubernetes context to use'
    )
    
    parser.add_argument(
        '--create-pod',
        action='store_true',
        help='Force creation of new pod (vs using existing)'
    )
    
    parser.add_argument(
        '--cleanup',
        action='store_true',
        help='Delete pod after execution (only for created pods)'
    )
    
    parser.add_argument(
        '--no-cleanup',
        action='store_true',
        help='Do not delete pod after execution'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Verbose output'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be executed without running'
    )
    
    parser.add_argument(
        '--list-jobs',
        action='store_true',
        help='List your recent kubexec jobs'
    )
    
    parser.add_argument(
        '--cleanup-old',
        type=int,
        metavar='HOURS',
        help='Cleanup your jobs older than specified hours'
    )
    
    parser.add_argument(
        '--all-users',
        action='store_true',
        help='With --list-jobs / --cleanup-old, act on every user\'s jobs in the namespace, not only yours'
    )
    
    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {__import__("kubexec").__version__}'
    )
    
    return parser


def main() -> int:
    """Main CLI entry point"""
    parser = create_parser()
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)
    
    try:
        # Load configuration
        config = Config(args.config)
        
        # Initialize Kubernetes client
        k8s_client = KubernetesClient(args.context, verify_ssl=config.get('verify_ssl', True))
        
        # Handle special operations
        if args.list_jobs:
            return _handle_list_jobs(k8s_client, config, args.namespace or config.get('namespace'), args.all_users)
        
        if args.cleanup_old:
            return _handle_cleanup_old(k8s_client, config, args.namespace or config.get('namespace'), args.cleanup_old, args.all_users)
        
        # Validate target
        if not args.target:
            parser.error("TARGET argument is required")
        
        # Resolve cleanup setting
        cleanup = None
        if args.cleanup:
            cleanup = True
        elif args.no_cleanup:
            cleanup = False
        
        # Execute command/script
        executor = KubeExecutor(config, k8s_client)
        
        exit_code, output = executor.execute(
            target=args.target,
            docker_image=args.docker_image,
            namespace=args.namespace,
            pod_name=args.pod_name,
            memory=args.memory,
            cpu=args.cpu,
            workdir=args.workdir,
            volume_mounts=args.volume_mounts,
            create_pod=args.create_pod,
            cleanup=cleanup,
            dry_run=args.dry_run
        )
        
        # Print output
        if output:
            print(output)
        
        return exit_code
        
    except KubeExecError as e:
        logger.error(f"kubexec error: {e}")
        return 1
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


def _handle_list_jobs(k8s_client: KubernetesClient, config: Config, namespace: str, all_users: bool = False) -> int:
    """Handle --list-jobs operation"""
    try:
        from .executor import KubeExecutor
        executor = KubeExecutor(config, k8s_client)
        jobs = executor.list_jobs(namespace, all_users)
        
        if not jobs:
            print("No kubexec jobs found" if all_users else "No kubexec jobs of yours found (use --all-users to see everyone's)")
            return 0
        
        user_header = f"{'USER':<20} " if all_users else ""
        print(f"{'NAME':<30} {user_header}{'STATUS':<12} {'IMAGE':<30} {'CREATED'}")
        print("-" * (90 + len(user_header)))
        for job in jobs:
            created = job['created'].strftime('%Y-%m-%d %H:%M:%S') if job['created'] else 'unknown'
            user_col = f"{job['user']:<20} " if all_users else ""
            print(f"{job['name']:<30} {user_col}{job['status']:<12} {job['image']:<30} {created}")
        
        return 0
        
    except Exception as e:
        logging.error(f"Failed to list jobs: {e}")
        return 1


def _handle_cleanup_old(k8s_client: KubernetesClient, config: Config, namespace: str, max_age_hours: int, all_users: bool = False) -> int:
    """Handle --cleanup-old operation"""
    try:
        from .executor import KubeExecutor
        executor = KubeExecutor(config, k8s_client)
        cleaned_count = executor.cleanup_old_jobs(namespace, max_age_hours, all_users)
        
        print(f"Cleaned up {cleaned_count} jobs older than {max_age_hours} hours" + ("" if all_users else " (yours only; use --all-users for everyone's)"))
        return 0
        
    except Exception as e:
        logging.error(f"Failed to cleanup old jobs: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())