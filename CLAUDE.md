# Cervo

A demo app for managing static website hosting on a shared VPS, built as an MCP server with [FastMCP](https://gofastmcp.com). Claude Code is the AI/chat interface used to exercise and test the server's tools during development. The whole environment — dev and production alike — runs from docker-compose: caddy is the front door, a worker process runs deployments, and mailcatcher stands in for SMTP in development.

## Running

Prerequisite: [Docker](https://www.docker.com/) (with Compose). [uv](https://docs.astral.sh/uv/) is used for lint/format and can run the test suite directly.

```bash
bin/dev    # docker compose up -d — the whole environment
```

The stack is four services, all sharing the `.data` volume at `/mnt/data`:

- `app` — the MCP server and the public website, one process. Publishes no port; caddy reverse-proxies it at `http://localhost` — the homepage (with docs at `/docs`) — and the MCP endpoint stays `http://localhost/mcp`.
- `worker` — the job worker (`cervo-worker`). Boots first, creates the database tables, and renders the initial Caddyfile.
- `caddy` — the front door on ports 80/443. Runs on the generated `/mnt/data/Caddyfile` and serves the sites at `http://{slug}.localhost`. Its unauthenticated admin API (`caddy:2019`) is reachable only on the compose network. On a fresh checkout it restarts until the worker first renders the Caddyfile — that's the `restart: unless-stopped` doing its job, not a bug.
- `mail` — mailcatcher (SMTP on 1025, web UI at http://localhost:1080). Development mail goes here, not to real SMTP.

`./src` (and `./tests`) are bind-mounted into `app` and `worker`, so after changing code run `docker compose restart app` (or `worker`) — no rebuild needed. Rebuild (`docker compose build`) only when dependencies change.

Lint and format with ruff before committing: `bin/lint` (runs `uv run ruff
check .` and `uv run ruff format .`).
Rule selection is `extend-select` in `pyproject.toml` — it adds to ruff's defaults rather
than replacing them, so keep it that way or the project lints less than it does now.

## Configuration

Settings live in `src/cervo/config.py`. Paths and service addresses are **constants**, not environment variables: the stack always runs from compose, so `/mnt/data` (the shared volume), the database at `/mnt/data/cervo.db`, the MCP bind (`0.0.0.0:8000`), caddy's admin API (`http://caddy:2019`), and the proxy upstream (`app:8000`) are the same everywhere, and an env var would only invite drift. Job tuning (attempts, retry delay, timeout, poll interval) is private module constants for the same reason.

What actually varies between environments is read from the environment / `.env` via python-decouple, with defaults correct for development — no `.env` file is needed in dev:

| Variable | Default | Purpose |
|---|---|---|
| `DOMAIN` | `localhost` | cervo is served at `{SCHEME}://{DOMAIN}`, sites at `{SCHEME}://{slug}.{DOMAIN}` |
| `SCHEME` | `http` | `https` in production: caddy then gets a certificate per hostname (persisted in its `/data` volume) and redirects plain http |
| `ACME_EMAIL` | *(empty)* | contact caddy registers with Let's Encrypt |
| `EMAIL_HOST` / `EMAIL_PORT` | `mail` / `1025` | SMTP (the mailcatcher service in dev) |
| `EMAIL_FROM` | `cervo@localhost` | From address on outgoing mail |
| `EMAIL_USER` / `EMAIL_PASSWORD` | *(empty)* | set for a real SMTP provider — switches `mail.send` to STARTTLS + login (port 587 shape) |
| `AUTH_CODE_TTL` | `600` | seconds an emailed sign-in code stays valid |
| `AUTH_SESSION_TTL` | `14400` | seconds a chat stays signed in (4 hours) |

## Layout

- `src/cervo/server.py` — the FastMCP instance (`app`) and all tool definitions.
  `authenticate` uses MCP elicitation to have the human confirm the address before
  any code is sent, so a client that cannot elicit cannot sign in.
- `src/cervo/web/` — the public website: pages built from FastHTML fasttags on
  the design system (`design-system/`), registered on the FastMCP app as custom
  HTTP routes (`web.register(app)` at the bottom of server.py). FastMCP appends
  custom routes after `/mcp`, so they cannot shadow it; among the pages the
  catch-all 404 route must stay registered last. The same components are the
  single source for a site's default page (`web.default_page`), which the
  worker renders and writes at deploy time.
- `src/cervo/worker.py` — the job worker: polls for due jobs, dispatches them by
  kind (`_HANDLERS`), reaps timed-out ones. Entry point `cervo-worker`.
- `src/cervo/config.py` — settings (see above)
- `src/cervo/user/`, `src/cervo/website/`, `src/cervo/auth/`, `src/cervo/job/` —
  one package per domain (see below)
- `src/cervo/schema.py` — `create_tables()`, the one place that knows every table
- `src/cervo/db.py` — `connect()`, the connection context manager
- `src/cervo/errors.py` — `AppError`, the base for failures the user should read
- `src/cervo/mail.py` — sending mail over SMTP
- `src/cervo/caddy.py` — rendering the Caddyfile from the database and reloading
  caddy over its admin API
- `src/cervo/templates/` — jinja2 templates: the Caddyfile and the MCP app
  pages (deployment progress, websites overview)
- `src/cervo/__init__.py` — `main()` entrypoint (the `app` service)
- `Dockerfile` — one image for `app`, `worker`, and the test runner
  (`ENTRYPOINT ["uv", "run"]`)
- `docker-compose.yml` — the dev environment (see Running);
  `docker-compose.test.yml` — the throwaway test stack (see Tests)
- `bin/` — the everyday commands: `dev`, `lint`, `test`, `smoke`

## Deploying

`bin/deploy` builds and pushes the image to Docker Hub and runs the ansible
playbook in `deploy/` against the VPS (podman quadlets, secrets from
1Password at deploy time). See the README's Deploying section for the
runbook; `deploy/inventory.yml` is gitignored on purpose.

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

`job` is generic on purpose: a row is a `kind` plus a JSON `payload`, so future
background work reuses the same queue, retry, and timeout machinery. Timestamps
and payload serialization never leave its `_dao`.

## Jobs and deployment

Creating a website inserts the row and enqueues the first job of the deploy
chain — the MCP server never provisions anything itself. A deployment is three
chained jobs, and the worker enqueues each next one in the same transaction
that marks its predecessor done: `website.provision` creates `DATA_DIR/{slug}/`
and writes the default `index.html` (only if missing — an owner's replaced
files are never clobbered), `website.configure` regenerates the whole Caddyfile
from the database, and `website.activate` POSTs it to caddy's `/load` admin
endpoint. Every step is idempotent, so retrying is always safe — and only the
failed step retries, not the whole chain. Rows with the legacy single-job kind
`website.deploy` (from before the chain) are still read and handled, so old
sites keep their status.

Because the deployment is now stepwise, a site also reports `step`,
`steps_done`, and `steps_total`, and `create_website` streams real-time
progress: when the client sent a `progressToken`, the tool follows the chain
with `ctx.report_progress` (one notification per step) and returns the site
already `live` (or `failed`); without a token it returns immediately as
status `pending`, and the deployment MCP app or `list_websites` follows
instead.

Job lifecycle: `pending → running → done`, or on failure back to `pending` with
`attempts + 1` and a retry delay, until `failed` for good after `_MAX_ATTEMPTS`.
A running job that outlives its `timeout` is reaped — counted as a failed attempt
and made pending again — which is also the crash recovery: a worker killed
mid-job needs no shutdown protocol. At startup the worker also "heals": it
renders and reloads the Caddyfile even with no jobs queued, so a fresh checkout
or restored data directory starts serving immediately.

Deleting a website (`delete_website`, owner-only) is the mirror image: the
row is deleted immediately — the slug frees up and the site stops being
listed — and a `website.delete` job has the worker re-render the Caddyfile
(dropping the route) and then remove `DATA_DIR/{slug}/`.

A site's `status`/`error` shown by the tools comes from the latest job of its
deploy chain (mid-chain reads as `deploying`; the final job's `done` as
`live`). Calling `create_website` on
your own failed site queues a fresh deployment; the slug `caddyfile` is reserved
(it would collide with `DATA_DIR/Caddyfile` on case-insensitive filesystems).

## Tests

```bash
bin/test    # unit suite, verbose; extra args go to pytest (bin/test tests/test_job.py)
bin/smoke   # the whole stack end to end, driven through a real MCP client
```

Both run in the throwaway test stack and tear it down afterwards — containers,
network, and volume — pass or fail (`bin/smoke` rebuilds every image first and
dumps the stack's logs when it failed). CI runs these same scripts as separate
steps, so the stacks never race each other.

Tests have their own compose file and project (`cervo-test`) holding the whole
stack, fully separate from dev: a named volume instead of `./.data`, no source
bind mounts — code and tests run as baked into the image, so the scripts
always pass `--build` — and **no published ports**, so it runs side by side
with the dev stack and there is no way for test and dev data (or ports) to
collide.

`tests/smoke.py` holds the end-to-end checks, run by their own `smoke`
service — `depends_on` pulls up the stack they exercise. From inside the test
network they cover the whole surface through real clients: every tool listed,
sign-in with the code verified in mailcatcher (sender, subject, wrong code
refused, declining sends nothing), sessions not leaking across conversations,
slug validation and ownership rules, and a site created, polled to `live`,
and its page actually fetched through caddy. The file is intentionally named
so a plain `pytest` run skips it (it needs the stack up); in the test stack
`DOMAIN` is set to `caddy`, so the front door is `http://caddy` and sites are
fetched with a Host header.

The unit suite is hermetic on top of the stack isolation (see below), so
`uv run pytest` on the host works too — CI (`.github/workflows/test.yml`)
lints with uv, then runs `bin/test` and `bin/smoke` on push to `main` and on
every pull request.

Tests never touch development data or services: autouse fixtures in
`tests/conftest.py` repoint `config.DATA_DIR` and `config.DATABASE_PATH` at a
per-test `tmp_path` (creating the tables there), replace `mail.send` with a
capture list, and replace `caddy.reload` the same way. All three are autouse — a
test cannot escape them by forgetting a fixture — and `tests/test_isolation.py`
asserts the guarantees hold.

Write tests against the MCP tools rather than the services: `chat()` returns a
client whose connection is one conversation (its own session id, so its own
sign-in) with a scripted human answering the elicitation, and `sign_in()` runs the
whole handshake. The website's pages are tested through a starlette `TestClient`
over `app.http_app()` (`tests/test_web.py`). Read codes out of the `mailbox` fixture the way a user reads their
inbox. The worker never runs as a process in tests — call `worker.run_once()` (or
the `deploy()` helper) for deterministic deployments.

Note `tests/__init__.py` is required: a transitive dependency (`caio`) installs a
top-level `tests` package that otherwise shadows this one.

## Testing with Claude Code

The server is registered as a project MCP server in `.mcp.json` at
`http://localhost/mcp` — through caddy, like production. When the stack is up, its
tools are available directly in the chat — the primary way to test is to just call
them and check the results.

- The stack must already be running (`docker compose up -d`) **before starting the Claude Code session** — Claude Code connects to MCP servers at session startup. If tool calls fail to connect, check `docker compose ps` (on a fresh checkout, give the worker a moment to render the Caddyfile so caddy stays up), then run `/mcp` to connect.
- Claude Code never reconnects automatically: whenever you change the MCP server code, `docker compose restart app` and run `/mcp` to reconnect. This is mandatory when tool schemas change (names, parameters, docstrings), since tool definitions are cached from the initial handshake; if only a tool's body changed, restarting the service is enough — the next call reaches the fresh process as long as the schema still matches. Worker-side changes (deployments) need only `docker compose restart worker`.
- For checks the MCP connection can't cover (error cases, raw protocol), use a throwaway `fastmcp.Client` script:

```python
import asyncio
from fastmcp import Client


async def main():
    async with Client("http://localhost/mcp") as client:
        print(await client.list_tools())


asyncio.run(main())
```
