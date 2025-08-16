"""List pods CLI for kubexec"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich import box
from .config import Config
from .k8s_client import KubernetesClient
from .exceptions import KubeExecError

console = Console()


def setup_logging(verbose: bool = False) -> None:
    """Setup logging configuration"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(message)s'
    )


def create_parser() -> argparse.ArgumentParser:
    """Create command-line argument parser"""
    parser = argparse.ArgumentParser(
        prog='kuberlist',
        description='List Kubernetes pods in current namespace',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  kuberlist                    # List all pods
  kuberlist --all              # List all pods with more details
  kuberlist --running          # Only running pods
  kuberlist --kubexec          # Only kubexec jobs
  kuberlist --watch            # Watch pod status changes
        """
    )
    
    parser.add_argument(
        '-n', '--namespace',
        help='Kubernetes namespace (auto-detected if not specified)'
    )
    
    parser.add_argument(
        '--context',
        help='Kubernetes context to use'
    )
    
    parser.add_argument(
        '--all',
        action='store_true',
        help='Show all pod details including node and age'
    )
    
    parser.add_argument(
        '--running',
        action='store_true',
        help='Show only running pods'
    )
    
    parser.add_argument(
        '--kubexec',
        action='store_true',
        help='Show only kubexec jobs'
    )
    
    parser.add_argument(
        '--watch',
        action='store_true',
        help='Watch for pod status changes (press Ctrl+C to stop)'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Verbose output'
    )
    
    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {__import__("kubexec").__version__}'
    )
    
    return parser


def format_age(created_time) -> str:
    """Format pod age in human readable format"""
    if not created_time:
        return "unknown"
    
    now = datetime.now(timezone.utc)
    age = now - created_time
    
    days = age.days
    hours, remainder = divmod(age.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    
    if days > 0:
        return f"{days}d{hours}h"
    elif hours > 0:
        return f"{hours}h{minutes}m"
    else:
        return f"{minutes}m"


def get_status_color(status: str, restart_count: int = 0) -> str:
    """Get color for pod status"""
    if status == "Running":
        return "green" if restart_count == 0 else "yellow"
    elif status == "Pending":
        return "yellow"
    elif status == "Succeeded":
        return "blue"
    elif status in ["Failed", "Error"]:
        return "red"
    else:
        return "white"


def list_pods(
    k8s_client: KubernetesClient,
    namespace: str,
    show_all: bool = False,
    running_only: bool = False,
    kubexec_only: bool = False
) -> None:
    """List pods in namespace"""
    try:
        # Set up label selector
        label_selector = None
        if kubexec_only:
            label_selector = "app=kubexec"
        
        pods = k8s_client.core_v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector
        )
        
        if not pods.items:
            filter_desc = ""
            if kubexec_only:
                filter_desc = " kubexec"
            elif running_only:
                filter_desc = " running"
            console.print(f"[yellow]No{filter_desc} pods found in namespace '{namespace}'[/yellow]")
            return
        
        # Filter running pods if requested
        if running_only:
            pods.items = [pod for pod in pods.items if pod.status.phase == "Running"]
        
        # Create Rich table
        table = Table(title=f"Pods in namespace: [bold cyan]{namespace}[/bold cyan]", box=box.ROUNDED)
        
        # Add columns
        table.add_column("NAME", style="bold", no_wrap=True)
        table.add_column("STATUS", justify="center")
        table.add_column("RESTARTS", justify="center")
        table.add_column("AGE", justify="center")
        
        if show_all:
            table.add_column("NODE", style="dim")
            table.add_column("IMAGE", style="dim", max_width=30)
        
        # Add rows
        for pod in pods.items:
            name = pod.metadata.name
            status = pod.status.phase
            
            # Get restart count
            restart_count = 0
            if pod.status.container_statuses:
                restart_count = sum(cs.restart_count for cs in pod.status.container_statuses)
            
            # Format age
            age = format_age(pod.metadata.creation_timestamp)
            
            # Color status based on state
            status_color = get_status_color(status, restart_count)
            status_text = Text(status, style=status_color)
            
            # Color restart count (red if > 0)
            restart_style = "red" if restart_count > 0 else "green"
            
            if show_all:
                node = pod.spec.node_name or "pending"
                
                # Get first container image
                image = "unknown"
                if pod.spec.containers:
                    image = pod.spec.containers[0].image
                    # Shorten long image names
                    if len(image) > 30:
                        parts = image.split('/')
                        image = '/'.join(parts[-2:]) if len(parts) > 1 else image[:30] + "..."
                
                table.add_row(
                    name,
                    status_text,
                    Text(str(restart_count), style=restart_style),
                    age,
                    node,
                    image
                )
            else:
                table.add_row(
                    name,
                    status_text,
                    Text(str(restart_count), style=restart_style),
                    age
                )
        
        console.print(table)
                
    except Exception as e:
        raise KubeExecError(f"Failed to list pods: {e}")


def watch_pods(
    k8s_client: KubernetesClient,
    namespace: str,
    kubexec_only: bool = False
) -> None:
    """Watch pod status changes"""
    import time
    
    console.print(f"[bold blue]Watching pods in namespace '{namespace}'[/bold blue] (press Ctrl+C to stop)")
    console.print()
    
    try:
        while True:
            # Clear screen
            console.clear()
            
            # Show timestamp
            timestamp = datetime.now().strftime('%H:%M:%S')
            console.print(f"[dim]Last updated: {timestamp}[/dim]")
            console.print()
            
            list_pods(k8s_client, namespace, show_all=True, kubexec_only=kubexec_only)
            
            time.sleep(5)
            
    except KeyboardInterrupt:
        console.print("\n[yellow]Watch stopped.[/yellow]")


def main() -> int:
    """Main CLI entry point"""
    parser = create_parser()
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.verbose)
    
    try:
        # Load configuration to get namespace
        config = Config()
        namespace = args.namespace or config.get('namespace')
        
        # Initialize Kubernetes client
        k8s_client = KubernetesClient(args.context)
        
        if args.watch:
            watch_pods(k8s_client, namespace, args.kubexec)
        else:
            list_pods(k8s_client, namespace, args.all, args.running, args.kubexec)
        
        return 0
        
    except KubeExecError as e:
        logging.error(f"kuberlist error: {e}")
        return 1
    except KeyboardInterrupt:
        logging.info("Interrupted by user")
        return 130
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())