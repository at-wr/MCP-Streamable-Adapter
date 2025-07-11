#!/usr/bin/env python3

import logging
import sys
from pathlib import Path

import click
import uvicorn
from rich.console import Console
from rich.table import Table
from rich import print as rprint

from config import AdapterConfig, ServerConfig, load_config, save_config
from http_server import MCPStreamableHTTPServer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@click.group()
def cli():
    pass


@cli.command()
@click.option('--config', '-c', default='servers.json', help='Configuration file path')
@click.option('--host', '-h', default='localhost', help='Host to bind to')
@click.option('--port', '-p', default=8080, help='Port to bind to')
@click.option('--debug', '-d', is_flag=True, help='Enable debug mode')
@click.option('--no-reload', is_flag=True, help='Disable auto-reload on config changes')
def serve(config: str, host: str, port: int, debug: bool, no_reload: bool):
    try:
        adapter_config = load_config(config)
        if host != 'localhost':
            adapter_config.host = host
        if port != 8080:
            adapter_config.port = port
        if debug:
            adapter_config.debug = debug
            logging.getLogger().setLevel(logging.DEBUG)
    except Exception as e:
        rprint(f"[red]Failed to load configuration: {e}[/red]")
        sys.exit(1)
    
    if not adapter_config.servers:
        rprint("[yellow]No servers configured. Please add servers to your configuration file.[/yellow]")
    
    # Show startup info
    enabled_servers = [s for s in adapter_config.servers if s.enabled]
    disabled_servers = [s for s in adapter_config.servers if not s.enabled]
    hot_reload = not no_reload and Path(config).exists()
    
    rprint(f"[green]Starting MCP Adapter on {adapter_config.host}:{adapter_config.port}[/green]")
    rprint(f"[blue]Configuration: {config}[/blue]")
    rprint(f"[blue]Servers: {len(enabled_servers)} enabled, {len(disabled_servers)} disabled[/blue]")
    if hot_reload:
        rprint("[blue]Hot-reload: enabled[/blue]")
    else:
        rprint("[yellow]Hot-reload: disabled[/yellow]")
    
    # Create and start the enhanced server
    server = MCPStreamableHTTPServer(config, adapter_config)
    
    # Add startup and shutdown events
    server.app.add_event_handler("startup", server.startup)
    server.app.add_event_handler("shutdown", server.shutdown)
    
    # Run the server
    uvicorn.run(
        server.app,
        host=adapter_config.host,
        port=adapter_config.port,
        log_level="debug" if adapter_config.debug else "info",
        reload=False  # We handle our own reloading
    )


@cli.command()
@click.option('--config', '-c', default='servers.json', help='Configuration file path')
@click.option('--format', type=click.Choice(['json', 'yaml']), default='json', help='Configuration format')
def init(config: str, format: str):
    """Initialize a sample configuration file."""
    
    if Path(config).exists():
        if not click.confirm(f"Configuration file {config} already exists. Overwrite?"):
            return
    
    # Create sample configuration
    sample_config = AdapterConfig(
        host="localhost",
        port=8080,
        debug=False,
        cors_origins=["*"],
        servers=[
            ServerConfig(
                name="fetch",
                command="uvx",
                args=["mcp-server-fetch"],
                disabled=False
            ),
            ServerConfig(
                name="filesystem", 
                command="npx",
                args=["-y", "@modelcontextprotocol/server-filesystem", "/path/to/directory"],
                disabled=True
            )
        ]
    )
    
    try:
        save_config(sample_config, config)
        logger.info(f"Sample configuration saved to {config}")
        logger.info("Please edit the configuration file to add your MCP servers.")
        if format == 'json':
            logger.info("Use servers.json format compatible with Claude Desktop.")
    except Exception as e:
        logger.error(f"Failed to save configuration: {e}")
        sys.exit(1)


@cli.command()
@click.option('--config', '-c', default='servers.json', help='Configuration file path')
@click.argument('name')
@click.argument('command')
@click.option('--args', multiple=True, help='Command arguments')
@click.option('--env', multiple=True, help='Environment variables (KEY=VALUE)')
def add_server(config: str, name: str, command: str, args, env):
    """Add a new MCP server to the configuration."""
    
    try:
        adapter_config = load_config(config)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)
    
    # Check if server already exists
    if any(s.name == name for s in adapter_config.servers):
        logger.error(f"Server {name} already exists")
        sys.exit(1)
    
    # Parse environment variables
    env_dict = {}
    for env_var in env:
        if '=' in env_var:
            key, value = env_var.split('=', 1)
            env_dict[key] = value
    
    # Add new server
    new_server = ServerConfig(
        name=name,
        command=command,
        args=list(args) if args else [],
        env=env_dict if env_dict else {},
        disabled=False
    )
    
    adapter_config.servers.append(new_server)
    
    try:
        save_config(adapter_config, config)
        logger.info(f"Added server {name} to configuration")
    except Exception as e:
        logger.error(f"Failed to save configuration: {e}")
        sys.exit(1)


@cli.command()
@click.option('--config', '-c', default='servers.json', help='Configuration file path')
def list_servers(config: str):
    """List configured MCP servers."""
    
    try:
        adapter_config = load_config(config)
    except Exception as e:
        rprint(f"[red]Failed to load configuration: {e}[/red]")
        sys.exit(1)
    
    if not adapter_config.servers:
        rprint("[yellow]No servers configured[/yellow]")
        return
    
    console = Console()
    table = Table(title="MCP Servers Configuration")
    
    table.add_column("Name", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Command", style="blue")
    table.add_column("Args", style="magenta")
    table.add_column("Environment", style="yellow")
    
    for server in adapter_config.servers:
        status = "✅ enabled" if server.enabled else "❌ disabled"
        command = server.command
        args = " ".join(server.args) if server.args else ""
        env_vars = ", ".join([f"{k}={v}" for k, v in server.env.items()]) if server.env else ""
        
        table.add_row(server.name, status, command, args, env_vars)
    
    console.print(table)

@cli.command() 
@click.option('--config', '-c', default='servers.json', help='Configuration file path')
@click.option('--host', '-h', default='localhost', help='Adapter host')
@click.option('--port', '-p', default=8080, help='Adapter port')
def status(config: str, host: str, port: int):
    """Show adapter status and running servers."""
    import requests
    
    try:
        # Check if adapter is running
        response = requests.get(f"http://{host}:{port}/health", timeout=5)
        health = response.json()
        
        rprint(f"[green]✅ MCP Adapter is running[/green]")
        rprint(f"[blue]Uptime: {health['uptime']:.1f} seconds[/blue]")
        rprint(f"[blue]Version: {health['version']}[/blue]")
        
        # Get server status
        response = requests.get(f"http://{host}:{port}/servers", timeout=5)
        servers = response.json()["servers"]
        
        console = Console()
        table = Table(title="Running MCP Servers")
        
        table.add_column("Name", style="cyan") 
        table.add_column("Status", style="green")
        table.add_column("Endpoint", style="blue")
        table.add_column("Requests", style="magenta")
        table.add_column("Errors", style="red")
        table.add_column("Avg Response", style="yellow")
        
        for server in servers:
            status = "🟢 running" if server["running"] else "🔴 stopped"
            endpoint = server["mcp_endpoint"]
            stats = server["stats"]
            requests_count = str(stats["requests"])
            errors_count = str(stats["errors"])
            avg_time = f"{stats['avg_response_time']:.3f}s" if stats["avg_response_time"] > 0 else "N/A"
            
            table.add_row(server["name"], status, endpoint, requests_count, errors_count, avg_time)
        
        console.print(table)
        
    except requests.exceptions.ConnectionError:
        rprint(f"[red]❌ MCP Adapter is not running on {host}:{port}[/red]")
        rprint(f"[yellow]Start it with: python main.py serve --config {config}[/yellow]")
    except Exception as e:
        rprint(f"[red]Error checking status: {e}[/red]")


if __name__ == "__main__":
    cli()