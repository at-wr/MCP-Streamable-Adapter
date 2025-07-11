import asyncio
import logging
from typing import Dict, Optional, Any, Callable, List
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from config import ServerConfig

logger = logging.getLogger(__name__)


class MCPStdioClient:
    """MCP STDIO client using the official SDK."""
    
    def __init__(self, config: ServerConfig):
        self.config = config
        self.session: Optional[ClientSession] = None
        self.running = False
        self.message_handlers: Dict[str, Callable] = {}
        
    async def start(self):
        """Start the MCP STDIO client connection."""
        if self.running:
            return
            
        try:
            # Create server parameters
            # Combine command and args into full command list
            full_command = [self.config.command] + (self.config.args or [])
            
            server_params = StdioServerParameters(
                command=full_command[0],
                args=full_command[1:] if len(full_command) > 1 else [],
                env=self.config.env or None
            )
            
            # Start the STDIO client
            stdio_transport = stdio_client(server_params)
            
            async with stdio_transport as (read, write):
                async with ClientSession(read, write) as session:
                    self.session = session
                    self.running = True
                    
                    # Initialize the session
                    await session.initialize()
                    
                    logger.info(f"Started MCP STDIO client: {self.config.name}")
                    
                    # Keep the connection alive
                    while self.running:
                        await asyncio.sleep(1)
                        
        except Exception as e:
            logger.error(f"Failed to start MCP client {self.config.name}: {e}")
            self.running = False
            raise
    
    async def stop(self):
        """Stop the MCP STDIO client connection."""
        self.running = False
        if self.session:
            # Session will be cleaned up when the context manager exits
            pass
        logger.info(f"Stopped MCP STDIO client: {self.config.name}")
    
    async def call_tool(self, name: str, arguments: Dict[str, Any] = None) -> Any:
        """Call a tool on the MCP server."""
        if not self.session:
            raise RuntimeError(f"Client {self.config.name} is not connected")
            
        try:
            result = await self.session.call_tool(name, arguments or {})
            logger.debug(f"Called tool {name} on {self.config.name}: {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to call tool {name} on {self.config.name}: {e}")
            raise
    
    async def list_tools(self) -> List[Any]:
        """List available tools from the MCP server."""
        if not self.session:
            raise RuntimeError(f"Client {self.config.name} is not connected")
            
        try:
            result = await self.session.list_tools()
            return result.tools
        except Exception as e:
            logger.error(f"Failed to list tools on {self.config.name}: {e}")
            raise
    
    async def list_resources(self) -> List[Any]:
        """List available resources from the MCP server."""
        if not self.session:
            raise RuntimeError(f"Client {self.config.name} is not connected")
            
        try:
            result = await self.session.list_resources()
            return result.resources
        except Exception as e:
            logger.error(f"Failed to list resources on {self.config.name}: {e}")
            raise
    
    async def read_resource(self, uri: str) -> Any:
        """Read a resource from the MCP server."""
        if not self.session:
            raise RuntimeError(f"Client {self.config.name} is not connected")
            
        try:
            result = await self.session.read_resource(uri)
            return result
        except Exception as e:
            logger.error(f"Failed to read resource {uri} on {self.config.name}: {e}")
            raise
    
    async def list_prompts(self) -> List[Any]:
        """List available prompts from the MCP server."""
        if not self.session:
            raise RuntimeError(f"Client {self.config.name} is not connected")
            
        try:
            result = await self.session.list_prompts()
            return result.prompts
        except Exception as e:
            logger.error(f"Failed to list prompts on {self.config.name}: {e}")
            raise
    
    async def get_prompt(self, name: str, arguments: Dict[str, Any] = None) -> Any:
        """Get a prompt from the MCP server."""
        if not self.session:
            raise RuntimeError(f"Client {self.config.name} is not connected")
            
        try:
            result = await self.session.get_prompt(name, arguments or {})
            return result
        except Exception as e:
            logger.error(f"Failed to get prompt {name} on {self.config.name}: {e}")
            raise


class MCPStdioManager:
    """Manager for multiple MCP STDIO clients."""
    
    def __init__(self):
        self.clients: Dict[str, MCPStdioClient] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
    
    async def add_server(self, config: ServerConfig) -> MCPStdioClient:
        """Add and start a new MCP STDIO client."""
        if config.name in self.clients:
            raise ValueError(f"Server {config.name} already exists")
            
        client = MCPStdioClient(config)
        self.clients[config.name] = client
        
        if config.enabled:
            # Start the client in a background task
            task = asyncio.create_task(client.start())
            self.tasks[config.name] = task
            
            # Wait a bit to ensure connection is established
            await asyncio.sleep(0.1)
            
        return client
    
    async def remove_server(self, name: str):
        """Remove and stop an MCP STDIO client."""
        if name in self.clients:
            await self.clients[name].stop()
            del self.clients[name]
            
        if name in self.tasks:
            self.tasks[name].cancel()
            try:
                await self.tasks[name]
            except asyncio.CancelledError:
                pass
            del self.tasks[name]
    
    async def get_client(self, name: str) -> Optional[MCPStdioClient]:
        """Get a client by name."""
        return self.clients.get(name)
    
    async def stop_all(self):
        """Stop all clients."""
        for client in self.clients.values():
            await client.stop()
            
        for task in self.tasks.values():
            task.cancel()
            
        # Wait for all tasks to complete
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            
        self.clients.clear()
        self.tasks.clear()