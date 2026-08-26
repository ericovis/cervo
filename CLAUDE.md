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
Rule selection is `extend-select` in `pyproject.toml` — it adds to ruff's defaults rather
than replacing them, so keep it that way or the project lints less than it does now.

Mail in development goes to mailcatcher, not real SMTP; verify sent mail at http://localhost:1080.

## Configuration

All settings live in `src/cervo/config.py`, read from the environment / `.env` via python-decouple. Every default is already correct for development — no `.env` file is needed; only create one to override values for non-dev setups. Never hardcode a value in server code that belongs in config — add it to `config.py` with a default and document it in the table below.

| Variable | Default | Purpose |
|---|---|---|
| `EMAIL_HOST` / `EMAIL_PORT` | `localhost` / `1025` | SMTP (mailcatcher in dev) |
| `EMAIL_FROM` | `cervo@localhost` | From address on outgoing mail |
| `DATA_DIR` | `<repo>/.data` | data directory, shared with the caddy container |
| `DATABASE_PATH` | `<DATA_DIR>/cervo.db` | SQLite database file |
| `BASE_DOMAIN` | `localhost` | domain the hosted sites are served under (`SITES_DOMAIN` in code) |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8000` | HTTP bind address |
| `AUTH_CODE_TTL` | `600` | seconds an emailed sign-in code stays valid |
| `AUTH_SESSION_TTL` | `14400` | seconds a chat stays signed in (4 hours) |

## Layout

- `src/cervo/server.py` — the FastMCP instance (`app`) and all tool definitions.
  `authenticate` uses MCP elicitation to have the human confirm the address before
  any code is sent, so a client that cannot elicit cannot sign in.
- `src/cervo/config.py` — settings (see above)
- `src/cervo/user/`, `src/cervo/website/`, `src/cervo/auth/` — one package per
  domain (see below)
- `src/cervo/schema.py` — `create_tables()`, the one place that knows every table
- `src/cervo/db.py` — `connect()`, the connection context manager
- `src/cervo/errors.py` — `AppError`, the base for failures the user should read
- `src/cervo/mail.py` — sending mail over SMTP
- `src/cervo/__init__.py` — `main()` entrypoint (`uv run cervo`)
- `docker-compose.yml` — caddy (ports 8080/8443, serves `.data`) and mailcatcher (SMTP on 1025, web UI at http://localhost:1080)

## Domains

Each domain is a package of three modules:

| module | holds | visible outside |
|---|---|---|
| `types.py` | pydantic shapes | yes |
| `_dao.py` | SQL and queries | **no** |
| `service.py` | the rules, and the only caller of `_dao` | yes |

The leading underscore on `_dao.py` is the point: it is the language's own way of
saying "private to this package", readable without any tooling. Ruff's `TID251`
(`flake8-tidy-imports.banned-api` in `pyproject.toml`) backs it up, catching every
route in — `import cervo.user._dao`, `from cervo.user import _dao`, and
`user._dao` attribute access — with each domain's own `service.py` exempted via
`per-file-ignores`. Add a new domain's `_dao` to that list.

A domain's `__init__.py` re-exports its types and service functions and lists them
in `__all__`. Note it cannot hide `_dao`: Python binds a submodule onto its parent
package as soon as anything imports it, which is why the naming and the lint rule
do the work instead.

Keep everything else private: module constants that only their own module uses are
underscore-prefixed (`_UPSERT`, `_MAX_ATTEMPTS`), and so are helpers.

`user` is the owner of things: a row is created the first time an address signs in,
and `website.user_id` references it. Sites belong to a user, not to an email string,
so one person can own many.

## Authentication

Confirming an email is the only identity check. A chat calls `authenticate`, the
user pastes back the code that was mailed, and the chat is signed in for
`AUTH_SESSION_TTL`. Sessions are keyed by MCP session id, so they last as long as
the conversation does and a new conversation starts signed out. `create_website`
takes its owner from the session, never from an argument — tools that act on a
user's sites should call `services.auth.require()` and let its error tell the
agent to re-authenticate.

## Tests

```bash
uv run pytest
```

Tests never touch development data: an autouse fixture in `tests/conftest.py`
repoints `config.DATA_DIR` and `config.DATABASE_PATH` at a per-test `tmp_path` and
creates the tables there, and another replaces `mail.send` with a capture
list, so nothing reaches `.data` or mailcatcher. Both are autouse — a test cannot
escape them by forgetting a fixture — and `tests/test_isolation.py` asserts the
guarantees hold.

Write tests against the MCP tools rather than the services: `chat()` returns a
client whose connection is one conversation (its own session id, so its own
sign-in) with a scripted human answering the elicitation, and `sign_in()` runs the
whole handshake. Read codes out of the `mailbox` fixture the way a user reads their
inbox.

Note `tests/__init__.py` is required: a transitive dependency (`caio`) installs a
top-level `tests` package that otherwise shadows this one.

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
