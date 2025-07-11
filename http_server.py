import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import FastAPI, Request, HTTPException, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette import EventSourceResponse
from watchfiles import awatch

from config import AdapterConfig, load_config
from mcp_stdio_client import MCPStdioManager

logger = logging.getLogger(__name__)


class MCPStreamableHTTPServer:
    def __init__(self, config_path: str, initial_config: AdapterConfig):
        self.config_path = Path(config_path)
        self.config = initial_config
        self.app = FastAPI(title="FlowDown Adapter", version="1.0.0")
        self.stdio_manager = MCPStdioManager()
        self.server_stats: Dict[str, Dict] = {}
        self.startup_time = time.time()
        self.shutdown_event = asyncio.Event()

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=initial_config.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        self.setup_routes()
        self.setup_middleware()

        self._file_watcher_task: Optional[asyncio.Task] = None
        self._health_monitor_task: Optional[asyncio.Task] = None

    def setup_middleware(self):
        @self.app.middleware("http")
        async def log_requests(request: Request, call_next):
            start_time = time.time()
            response = await call_next(request)
            process_time = time.time() - start_time

            logger.info(f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s")

            if '/servers/' in request.url.path:
                server_name = request.url.path.split('/servers/')[1].split('/')[0]
                if server_name not in self.server_stats:
                    self.server_stats[server_name] = {
                        "requests": 0, "errors": 0, "avg_response_time": 0.0, "last_request": None
                    }

                stats = self.server_stats[server_name]
                stats["requests"] += 1
                stats["last_request"] = time.time()
                stats["avg_response_time"] = (stats["avg_response_time"] + process_time) / 2 if stats["avg_response_time"] > 0 else process_time

                if response.status_code >= 400:
                    stats["errors"] += 1

            return response

    def setup_routes(self):
        @self.app.post("/servers/{server_name}/mcp")
        async def handle_mcp_post(request: Request, server_name: str):
            return await self._handle_post_request(request, server_name)

        @self.app.get("/servers/{server_name}/mcp")
        async def handle_mcp_get(request: Request, server_name: str):
            return await self._handle_get_request(request, server_name)

        @self.app.get("/servers")
        async def list_servers():
            return {
                "servers": [
                    {
                        "name": name,
                        "running": client.running,
                        "config": client.config.dict(),
                        "mcp_endpoint": f"/servers/{name}/mcp",
                        "stats": self.server_stats.get(name, {
                            "requests": 0, "errors": 0, "avg_response_time": 0.0, "last_request": None
                        })
                    }
                    for name, client in self.stdio_manager.clients.items()
                ]
            }

        @self.app.get("/servers/{server_name}/status")
        async def get_server_status(server_name: str):
            client = await self.stdio_manager.get_client(server_name)
            if not client:
                raise HTTPException(status_code=404, detail=f"Server {server_name} not found")

            capabilities = {}
            if client.running:
                try:
                    tools = await client.list_tools()
                    resources = await client.list_resources()
                    prompts = await client.list_prompts()
                    capabilities = {"tools": len(tools), "resources": len(resources), "prompts": len(prompts)}
                except Exception as e:
                    logger.warning(f"Could not get capabilities for {server_name}: {e}")

            return {
                "name": server_name,
                "running": client.running,
                "config": client.config.dict(),
                "stats": self.server_stats.get(server_name, {}),
                "capabilities": capabilities
            }

        @self.app.post("/servers/{server_name}/restart")
        async def restart_server(server_name: str, background_tasks: BackgroundTasks):
            client = await self.stdio_manager.get_client(server_name)
            if not client:
                raise HTTPException(status_code=404, detail=f"Server {server_name} not found")
            background_tasks.add_task(self._restart_server, server_name)
            return {"message": f"Restarting server {server_name}"}

        @self.app.post("/reload-config")
        async def reload_config(background_tasks: BackgroundTasks):
            background_tasks.add_task(self._reload_config)
            return {"message": "Configuration reload initiated"}

        @self.app.get("/health")
        async def health_check():
            running_servers = sum(1 for client in self.stdio_manager.clients.values() if client.running)
            total_servers = len(self.stdio_manager.clients)
            return {
                "status": "healthy" if running_servers > 0 else "degraded",
                "uptime": time.time() - self.startup_time,
                "servers": {"total": total_servers, "running": running_servers, "stopped": total_servers - running_servers},
                "version": "1.0.0"
            }

        @self.app.get("/")
        async def root():
            return {
                "name": "FlowDown Adapter",
                "version": "1.0.0",
                "uptime": time.time() - self.startup_time,
                "endpoints": {"servers": "/servers", "health": "/health", "mcp_pattern": "/servers/{server_name}/mcp"}
            }

    async def _handle_post_request(self, request: Request, server_name: str):
        """Handle POST requests with JSON-RPC messages."""
        try:
            # Get the MCP client
            client = await self.stdio_manager.get_client(server_name)
            if not client:
                raise HTTPException(status_code=404, detail=f"Server {server_name} not found")

            if not client.running:
                raise HTTPException(status_code=503, detail=f"Server {server_name} not running")

            # Parse the JSON-RPC message(s)
            body = await request.body()
            content_type = request.headers.get("content-type", "")

            if not content_type.startswith("application/json"):
                raise HTTPException(status_code=400, detail="Content-Type must be application/json")

            try:
                data = json.loads(body.decode('utf-8'))
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON")

            # Handle single message or batch
            if isinstance(data, list):
                # Batch request
                responses = []
                for message in data:
                    response = await self._process_jsonrpc_message(client, message)
                    if response:
                        responses.append(response)

                if responses:
                    return responses
                else:
                    # No responses for notifications only
                    return Response(status_code=202)
            else:
                # Single message
                response = await self._process_jsonrpc_message(client, data)
                if response:
                    return response
                else:
                    # No response for notification
                    return Response(status_code=202)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error handling POST request: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _handle_get_request(self, request: Request, server_name: str):
        """Handle GET requests for Streamable HTTP."""
        try:
            # Get the MCP client
            client = await self.stdio_manager.get_client(server_name)
            if not client:
                raise HTTPException(status_code=404, detail=f"Server {server_name} not found")

            if not client.running:
                raise HTTPException(status_code=503, detail=f"Server {server_name} not running")

            # For Streamable HTTP, GET can be used to establish streaming
            # Check Accept header to determine response type
            accept_header = request.headers.get("accept", "")

            if "text/event-stream" in accept_header:
                # Client wants Server-Sent Events for streaming
                return EventSourceResponse(self._stream_generator(client, server_name))
            else:
                # Regular HTTP response
                return {
                    "server": server_name,
                    "status": "ready",
                    "protocol": "MCP Streamable HTTP"
                }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error handling GET request: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _process_jsonrpc_message(self, client, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process a single JSON-RPC message."""
        method = message.get("method")
        params = message.get("params", {})
        msg_id = message.get("id")

        try:
            # Handle MCP initialization
            if method == "initialize":
                client_info = params.get("clientInfo", {})
                protocol_version = params.get("protocolVersion")
                capabilities = params.get("capabilities", {})

                if msg_id is not None:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "protocolVersion": "2025-03-26",
                            "serverInfo": {
                                "name": "FlowDown Adapter",
                                "version": "1.0.0"
                            },
                            "capabilities": {
                                "tools": {},
                                "resources": {},
                                "prompts": {},
                                "logging": {}
                            }
                        }
                    }

            elif method == "notifications/initialized":
                # Client signals initialization is complete
                # This is a notification, so no response needed
                return None

            elif method == "tools/call":
                tool_name = params.get("name")
                tool_arguments = params.get("arguments", {})
                result = await client.call_tool(tool_name, tool_arguments)

                if msg_id is not None:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": result
                    }

            elif method == "tools/list":
                tools = await client.list_tools()

                if msg_id is not None:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {"tools": tools}
                    }

            elif method == "resources/list":
                resources = await client.list_resources()

                if msg_id is not None:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {"resources": resources}
                    }

            elif method == "resources/read":
                uri = params.get("uri")
                resource = await client.read_resource(uri)

                if msg_id is not None:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": resource
                    }

            elif method == "prompts/list":
                prompts = await client.list_prompts()

                if msg_id is not None:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {"prompts": prompts}
                    }

            elif method == "prompts/get":
                name = params.get("name")
                arguments = params.get("arguments", {})
                prompt = await client.get_prompt(name, arguments)

                if msg_id is not None:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": prompt
                    }

            elif method == "ping":
                # Handle ping requests
                if msg_id is not None:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {}
                    }

            else:
                # Unknown method
                if msg_id is not None:
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}"
                        }
                    }

        except Exception as e:
            logger.error(f"Error processing message {method}: {e}")
            if msg_id is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32603,
                        "message": str(e)
                    }
                }

        return None

    async def _stream_generator(self, client, server_name: str):
        """Generate streaming events for Streamable HTTP (optional SSE support)."""
        session_id = f"{server_name}_{id(client)}"

        try:
            # Send initial connection event
            yield {
                "event": "connected",
                "data": json.dumps({
                    "server": server_name,
                    "timestamp": asyncio.get_event_loop().time(),
                    "protocol": "MCP Streamable HTTP"
                })
            }

            while client.running:
                # Send heartbeat
                yield {
                    "event": "heartbeat",
                    "data": json.dumps({"timestamp": asyncio.get_event_loop().time()})
                }

                await asyncio.sleep(30)  # Heartbeat every 30 seconds

        except asyncio.CancelledError:
            logger.info(f"Stream cancelled for {session_id}")
        except Exception as e:
            logger.error(f"Error in stream for {session_id}: {e}")

    async def start_servers(self):
        """Start all configured MCP servers."""
        enabled_count = 0
        disabled_count = 0

        for server_config in self.config.servers:
            if server_config.enabled:
                try:
                    await self.stdio_manager.add_server(server_config)
                    logger.info(f"Started MCP server: {server_config.name}")
                    enabled_count += 1
                except Exception as e:
                    logger.error(f"Failed to start server {server_config.name}: {e}")
            else:
                logger.debug(f"Skipped disabled server: {server_config.name}")
                disabled_count += 1

        logger.info(f"Server startup complete: {enabled_count} enabled, {disabled_count} disabled")

        # Log MCP endpoints for enabled servers
        if enabled_count > 0:
            logger.info("Available MCP endpoints:")
            for server_config in self.config.servers:
                if server_config.enabled:
                    endpoint_url = f"http://{self.config.host}:{self.config.port}/servers/{server_config.name}/mcp"
                    logger.info(f"  - {server_config.name}: {endpoint_url}")

    async def stop_servers(self):
        """Stop all MCP servers."""
        await self.stdio_manager.stop_all()

    async def _restart_server(self, server_name: str):
        """Restart a specific server."""
        try:
            client = await self.stdio_manager.get_client(server_name)
            if client:
                config = client.config
                await self.stdio_manager.remove_server(server_name)
                await asyncio.sleep(1)  # Give it a moment
                await self.stdio_manager.add_server(config)
                logger.info(f"Restarted server: {server_name}")
        except Exception as e:
            logger.error(f"Failed to restart server {server_name}: {e}")

    async def _reload_config(self):
        """Reload configuration from file."""
        try:
            logger.info(f"Reloading configuration from {self.config_path}")
            new_config = load_config(str(self.config_path))

            # Find servers to add, update, or remove
            old_servers = {s.name: s for s in self.config.servers}
            new_servers = {s.name: s for s in new_config.servers}

            # Remove servers that are no longer in config
            for name in old_servers:
                if name not in new_servers:
                    await self.stdio_manager.remove_server(name)
                    logger.info(f"Removed server: {name}")

            # Add or update servers
            for name, server_config in new_servers.items():
                if name in old_servers:
                    old_config = old_servers[name]
                    # Check if config changed
                    if (old_config.command != server_config.command or
                        old_config.args != server_config.args or
                        old_config.env != server_config.env or
                        old_config.disabled != server_config.disabled):
                        # Restart with new config
                        await self.stdio_manager.remove_server(name)
                        await asyncio.sleep(0.5)
                        if not server_config.disabled:
                            await self.stdio_manager.add_server(server_config)
                        logger.info(f"Updated server: {name}")
                else:
                    # New server
                    if not server_config.disabled:
                        await self.stdio_manager.add_server(server_config)
                        logger.info(f"Added server: {name}")

            # Update adapter config
            self.config = new_config
            logger.info("Configuration reloaded successfully")

        except Exception as e:
            logger.error(f"Failed to reload configuration: {e}")

    async def start_file_watcher(self):
        """Start watching configuration file for changes."""
        if not self.config_path.exists():
            logger.warning(f"Configuration file {self.config_path} does not exist")
            return

        async def watch_config():
            try:
                async for changes in awatch(self.config_path):
                    logger.info(f"Configuration file changed: {changes}")
                    await self._reload_config()
            except Exception as e:
                logger.error(f"Error in file watcher: {e}")

        self._file_watcher_task = asyncio.create_task(watch_config())
        logger.info(f"Started file watcher for {self.config_path}")

    async def start_health_monitor(self):
        """Start health monitoring task."""
        async def monitor_health():
            while not self.shutdown_event.is_set():
                try:
                    # Check server health periodically
                    for name, client in self.stdio_manager.clients.items():
                        if client.running:
                            try:
                                # Simple ping to check if server is responsive
                                # This is optional and might not be supported by all servers
                                pass
                            except Exception as e:
                                logger.warning(f"Health check failed for {name}: {e}")

                    await asyncio.sleep(60)  # Check every minute
                except Exception as e:
                    logger.error(f"Error in health monitor: {e}")
                    await asyncio.sleep(60)

        self._health_monitor_task = asyncio.create_task(monitor_health())
        logger.info("Started health monitor")

    async def startup(self):
        await self.start_servers()
        if self.config_path.exists():
            await self.start_file_watcher()
        await self.start_health_monitor()

    async def shutdown(self):
        """Application shutdown."""
        logger.info("Shutting down MCP adapter...")
        self.shutdown_event.set()

        # Cancel background tasks
        if self._file_watcher_task:
            self._file_watcher_task.cancel()
        if self._health_monitor_task:
            self._health_monitor_task.cancel()

        # Stop all servers
        await self.stop_servers()

        logger.info("Shutdown complete")

    def run(self):
        """Run the HTTP server."""
        uvicorn.run(
            self.app,
            host=self.config.host,
            port=self.config.port,
            log_level="debug" if self.config.debug else "info"
        )
