# MCP Streamable Adapter

An adapter that bridges MCP Streamable HTTP clients with MCP STDIO servers, enabling HTTP-based clients to communicate with local subprocess MCP servers. Optimized for [FlowDown](https://github.com/Lakr233/FlowDown) v2.2.

## Features

- **Protocol Bridge**: Converts Streamable HTTP requests to STDIO communication
- **Multiple Servers**: Support for multiple MCP servers simultaneously
- **Claude Desktop Compatible**: Uses the same `servers.json` configuration format
- **Official MCP SDK**: Built using the official MCP Python SDK
- **Flexible Configuration**: Support for both JSON and YAML configuration files
- **Hot Configuration Reload**: Automatically detect and reload configuration changes
- **Server Monitoring**: Real-time server status, statistics, and health checks
- **Rich CLI**: Beautiful command-line interface with colored output and tables
- **Graceful Shutdown**: Proper cleanup of all resources on shutdown

## Architecture

```
FlowDown Client (Streamable HTTP) ──HTTP──> Adapter ──STDIO──> MCP Server (subprocess)
```

**Endpoints:**
- `POST /servers/{server_name}/mcp` - Send JSON-RPC requests
- `GET /servers/{server_name}/mcp` - Optional streaming support
- `GET /servers` - List available servers with stats
- `GET /servers/{server_name}/status` - Get detailed server status
- `POST /servers/{server_name}/restart` - Restart a specific server
- `POST /reload-config` - Reload configuration
- `GET /health` - Health check endpoint
- `GET /docs` - API documentation

## Installation
### Without venv
```bash
git clone https://github.com/at-wr/MCP-Streamable-Adapter
cd MCP-Streamable-Adapter
pip install -r requirements.txt
```

### With venv
```bash
git clone https://github.com/at-wr/MCP-Streamable-Adapter
cd MCP-Streamable-Adapter
python -m venv venv
chmod +x activate.sh
./activate.sh
```

## Configuration

### Using servers.json (Claude Desktop Compatible)

Create a `servers.json` file:

```json
{
  "mcpServers": {
    "fetch": {
      "disabled": false,
      "timeout": 60,
      "command": "uvx",
      "args": ["mcp-server-fetch"]
    },
    "github": {
      "timeout": 60,
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "<YOUR_TOKEN>"
      }
    },
    "filesystem": {
      "disabled": false,
      "timeout": 30,
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/directory"]
    }
  }
}
```

### Configuration Options

- `command`: Executable command (string, **required**)
- `args`: Command arguments (array, *optional*)
- `env`: Environment variables (object, *optional*)
- `timeout`: Request timeout in seconds (number, *optional*, default: 60)
- `disabled`: Whether server is disabled (boolean, *optional*, default: false)
- `cwd`: Working directory (string, *optional*)

## Usage

### Start the adapter

```bash
# Basic usage (hot-reload enabled by default)
python main.py serve

# Custom host/port
python main.py serve --host 0.0.0.0 --port 9000

# Disable hot-reload
python main.py serve --no-reload
```

### Initialize configuration

```bash
# Create sample servers.json
python main.py init

# Create YAML configuration
python main.py init --config config.yaml --format yaml
```

### Manage servers

```bash
# Add a server
python main.py add-server fetch uvx --args mcp-server-fetch

# Add server with environment variables
python main.py add-server github npx --args "-y" "@modelcontextprotocol/server-github" --env GITHUB_PERSONAL_ACCESS_TOKEN=your_token

# List configured servers (with nice table)
python main.py list-servers

# Check adapter and server status
python main.py status
```

## API Usage

### Monitor and manage

```bash
# Check health
curl http://localhost:8080/health

# List servers with stats
curl http://localhost:8080/servers

# Get specific server status
curl http://localhost:8080/servers/fetch/status

# Restart a server
curl -X POST http://localhost:8080/servers/fetch/restart

# Reload configuration
curl -X POST http://localhost:8080/reload-config
```

### Call a tool

```bash
curl -X POST http://localhost:8080/servers/fetch/mcp \\
  -H "Content-Type: application/json" \\
  -H "Accept: application/json" \\
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "fetch",
      "arguments": {
        "url": "https://api.github.com"
      }
    }
  }'
```

### List tools

```bash
curl -X POST http://localhost:8080/servers/fetch/mcp \\
  -H "Content-Type: application/json" \\
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list"
  }'
```

### Batch requests

```bash
curl -X POST http://localhost:8080/servers/fetch/mcp \\
  -H "Content-Type: application/json" \\
  -d '[
    {
      "jsonrpc": "2.0",
      "id": 1,
      "method": "tools/list"
    },
    {
      "jsonrpc": "2.0",
      "id": 2,
      "method": "resources/list"
    }
  ]'
```

## Supported MCP Methods

- `tools/call` - Call a tool with arguments
- `tools/list` - List available tools
- `resources/list` - List available resources
- `resources/read` - Read a resource by URI
- `prompts/list` - List available prompts
- `prompts/get` - Get a prompt with arguments

## Example Integration

Your Streamable HTTP client can connect to any configured server:

```javascript
// Connect to the fetch server
const response = await fetch('http://localhost:8080/servers/fetch/mcp', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Accept': 'application/json,text/event-stream'
  },
  body: JSON.stringify({
    jsonrpc: '2.0',
    id: 1,
    method: 'tools/call',
    params: {
      name: 'fetch',
      arguments: { url: 'https://api.example.com' }
    }
  })
});
```

## Error Handling

The adapter provides proper HTTP status codes and JSON-RPC error responses:

- `404` - Server not found
- `503` - Server not running
- `400` - Invalid JSON or Content-Type
- `500` - Internal server error

JSON-RPC errors follow the standard format:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32601,
    "message": "Method not found: unknown/method"
  }
}
```

## Development

### Project Structure

```
├── main.py              # CLI entry point
├── config.py            # Configuration management
├── mcp_stdio_client.py  # MCP STDIO client using official SDK
├── http_server.py       # FastAPI HTTP server
├── servers.json         # Example configuration
└── requirements.txt     # Dependencies
```

### Contributing

1. Ensure the official MCP Python SDK is used for all MCP protocol handling
2. Follow the Streamable HTTP specification for HTTP endpoints
3. Maintain compatibility with Claude Desktop's `servers.json` format
4. Add proper error handling and logging

## License

MIT License
