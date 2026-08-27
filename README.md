# Cervo

A demo app for managing static website hosting on a shared VPS. It's built as an MCP server ([FastMCP](https://gofastmcp.com), served over HTTP), so the hosting is managed by chatting with an AI: in development, [Claude Code](https://claude.com/claude-code) acts as the chat interface for exercising and testing the server's tools.

## Quick start

Prerequisite: [Docker](https://www.docker.com/) (with Compose).

```bash
bin/dev    # docker compose up -d: app, worker, caddy, mail
```

Caddy fronts everything on port 80: the MCP server at `http://localhost/mcp`, and each created site at `http://{slug}.localhost`.

Then open Claude Code in this repo — the server is pre-registered in `.mcp.json`, so its tools become available directly in the chat. Start the stack *before* opening the Claude Code session (connections are made at startup), and run `/mcp` to reconnect whenever you change the MCP server code (`docker compose restart app`) — Claude Code doesn't reconnect automatically.

Development works with zero configuration; settings can be overridden via a `.env` file (see the [configuration table](CLAUDE.md#configuration)).

## Documentation

Detailed information lives in [CLAUDE.md](CLAUDE.md) (also loaded by Claude Code as project context):

- [Configuration](CLAUDE.md#configuration) — all settings and their defaults
- [Layout](CLAUDE.md#layout) — where things live in the codebase
- [Testing with Claude Code](CLAUDE.md#testing-with-claude-code) — the development/testing workflow
