from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
import yaml
import json


class ServerConfig(BaseModel):
    name: str
    command: str
    args: Optional[List[str]] = []
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = {}
    timeout: Optional[int] = 60
    disabled: bool = False
    
    @property
    def enabled(self) -> bool:
        return not self.disabled


class AdapterConfig(BaseModel):
    host: str = "localhost"
    port: int = 8080
    debug: bool = False
    cors_origins: List[str] = ["*"]
    servers: List[ServerConfig] = []


def load_config(config_path: str) -> AdapterConfig:
    try:
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f) if config_path.endswith(('.yaml', '.yml')) else json.load(f)
        
        if "mcpServers" in data:
            return _load_servers_json_format(data)
        return AdapterConfig(**data)
        
    except FileNotFoundError:
        return AdapterConfig()
    except Exception as e:
        raise ValueError(f"Invalid configuration file: {e}")


def _load_servers_json_format(data: Dict[str, Any]) -> AdapterConfig:
    """Convert servers.json format to AdapterConfig."""
    mcp_servers = data["mcpServers"]
    
    servers = []
    for name, config in mcp_servers.items():
        server_config = ServerConfig(
            name=name,
            command=config["command"],
            args=config.get("args", []),
            cwd=config.get("cwd"),
            env=config.get("env", {}),
            timeout=config.get("timeout", 60),
            disabled=config.get("disabled", False)
        )
        servers.append(server_config)
    
    return AdapterConfig(
        host=data.get("host", "localhost"),
        port=data.get("port", 8080),
        debug=data.get("debug", False),
        cors_origins=data.get("cors_origins", ["*"]),
        servers=servers
    )


def save_config(config: AdapterConfig, config_path: str) -> None:
    """Save configuration to file."""
    if config_path.endswith('.json') and 'servers.json' in config_path:
        # Save in servers.json format
        _save_servers_json_format(config, config_path)
    else:
        # Save in YAML format
        with open(config_path, 'w') as f:
            yaml.dump(config.dict(), f, default_flow_style=False)


def _save_servers_json_format(config: AdapterConfig, config_path: str) -> None:
    """Save in servers.json format (Claude Desktop style)."""
    mcp_servers = {}
    for server in config.servers:
        mcp_servers[server.name] = {
            "command": server.command,
            "args": server.args,
            "timeout": server.timeout,
            "disabled": server.disabled
        }
        if server.env:
            mcp_servers[server.name]["env"] = server.env
        if server.cwd:
            mcp_servers[server.name]["cwd"] = server.cwd
    
    data = {
        "mcpServers": mcp_servers
    }
    
    # Add adapter settings if not default
    if config.host != "localhost":
        data["host"] = config.host
    if config.port != 8080:
        data["port"] = config.port
    if config.debug:
        data["debug"] = config.debug
    if config.cors_origins != ["*"]:
        data["cors_origins"] = config.cors_origins
    
    with open(config_path, 'w') as f:
        json.dump(data, f, indent=2)