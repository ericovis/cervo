# Cervo

A demo app for managing static website hosting on a shared VPS, built as an MCP server with [FastMCP](https://gofastmcp.com). Claude Code is the AI/chat interface used to exercise and test the server's tools during development. The dev environment mirrors the VPS: caddy serves the hosted sites from `.data`, and mailcatcher stands in for SMTP.

## Running

Prerequisites: [uv](https://docs.astral.sh/uv/) and [Docker](https://www.docker.com/) (with Compose) must be installed.

```bash
uv sync                 # install deps
uv run cervo            # start the MCP server (HTTP)
docker compose up -d    # supporting services (caddy, mailcatcher)
```

The server always runs over HTTP (stdio is intentionally not used), at `http://127.0.0.1:8000/mcp` by default.

Lint and format with ruff before committing: `uv run ruff check .` and `uv run ruff format .`.

Mail in development goes to mailcatcher, not real SMTP; verify sent mail at http://localhost:1080.

## Configuration

All settings live in `src/cervo/config.py`, read from the environment / `.env` via python-decouple. Every default is already correct for development — no `.env` file is needed; only create one to override values for non-dev setups. Never hardcode a value in server code that belongs in config — add it to `config.py` with a default and document it in the table below.

| Variable | Default | Purpose |
|---|---|---|
| `EMAIL_HOST` / `EMAIL_PORT` | `localhost` / `1025` | SMTP (mailcatcher in dev) |
| `DATA_DIR` | `<repo>/.data` | data directory, shared with the caddy container |
| `BASE_DOMAIN` | `localhost` | domain the hosted sites are served under (`SITES_DOMAIN` in code) |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8000` | HTTP bind address |

## Layout

- `src/cervo/server.py` — the FastMCP instance (`app`) and all tool definitions
- `src/cervo/config.py` — settings (see above)
- `src/cervo/__init__.py` — `main()` entrypoint (`uv run cervo`)
- `docker-compose.yml` — caddy (ports 8080/8443, serves `.data`) and mailcatcher (SMTP on 1025, web UI at http://localhost:1080)

## Testing with Claude Code

The server is registered as a project MCP server in `.mcp.json`, so when it's running, its tools are available directly in the chat — the primary way to test is to just call them and check the results.

- The server must already be running (`uv run cervo`) **before starting the Claude Code session** — Claude Code does not start it, and it connects to MCP servers at session startup. If the server wasn't up when the session began (or tool calls fail to connect), check that it's running and that `MCP_PORT` matches `.mcp.json`, then run `/mcp` to connect.
- Claude Code never reconnects automatically: whenever you change the MCP server code, restart the server and run `/mcp` to reconnect. This is mandatory when tool schemas change (names, parameters, docstrings), since tool definitions are cached from the initial handshake; if only a tool's body changed, restarting the server is enough — the next call reaches the fresh process as long as the schema still matches.
- For checks the MCP connection can't cover (error cases, raw protocol), use a throwaway `fastmcp.Client` script:

```python
import asyncio
from fastmcp import Client


async def main():
    async with Client("http://127.0.0.1:8000/mcp") as client:
        print(await client.list_tools())


asyncio.run(main())
```
