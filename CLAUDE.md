# cervo

A demo app for managing static website hosting on a shared VPS, built as an MCP server with [FastMCP](https://gofastmcp.com). Claude Code is the AI/chat interface used to exercise and test the server's tools during development. The whole environment — dev and production alike — runs from docker-compose: caddy is the front door, a worker process runs deployments, and mailcatcher stands in for SMTP in development.

## Running

Prerequisite: [Docker](https://www.docker.com/) (with Compose). [uv](https://docs.astral.sh/uv/) is used for lint/format and can run the test suite directly.

```bash
bin/dev    # docker compose up -d — the whole environment
```

The stack is four services, all sharing the `.data` volume at `/mnt/data`:

- `app` — the MCP server and the public website, one service: uvicorn with
  `WEB_CONCURRENCY` worker processes (default 1) importing `cervo.asgi` —
  MCP runs stateless, so any worker serves any request. Publishes no port; caddy reverse-proxies it at `http://localhost` — the homepage (with docs at `/docs`) — and the MCP endpoint stays `http://localhost/mcp`.
- `worker` — the job worker (`cervo-worker`): one container running
  `WORKER_CONCURRENCY` polling threads (default 1) — claiming is atomic in
  the database, so more threads never double-run a job. Boots first, creates
  the database tables, and renders the initial Caddyfile (once, before any
  thread polls).
- `caddy` — the front door on ports 80/443. Runs on the generated `/mnt/data/Caddyfile` and serves the sites at `http://{slug}.localhost`. Its unauthenticated admin API (`caddy:2019`) is reachable only on the compose network. On a fresh checkout it restarts until the worker first renders the Caddyfile — that's the `restart: unless-stopped` doing its job, not a bug.
- `mail` — mailcatcher (SMTP on 1025, web UI at http://localhost:1080). Development mail — the sign-in codes — goes here, not to real SMTP.

Every cervo container runs unprivileged inside: the image bakes in a
`cervo` user (uid 1000) that owns `/app` and `/mnt/data`, and caddy runs
as the same uid with a sysctl re-allowing :80/:443 (its dev state goes to
`/tmp` — certificates only exist in production, where its volumes are
handed over with `:U`). Host-side, the runtimes still run as root. If a
`.data` directory written by an older root-run stack refuses writes,
delete it (dev data is disposable) or chown it to uid 1000.

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
| `ACME_EMAIL` | *(empty)* | ACME contact for cervo's own hostname; each hosted site registers its owner's email instead |
| `WEB_CONCURRENCY` | `1` | uvicorn worker processes serving `app`; safe to raise — MCP is stateless and SQLite runs in WAL |
| `WORKER_CONCURRENCY` | `1` | polling threads in the `worker` container; safe to raise — job claiming is atomic and the Caddyfile kinds are serialized |
| `HONEYBADGER_API_KEY` | *(empty)* | Honeybadger project key; empty (dev, tests) disables error reporting and Insights entirely |
| `HONEYBADGER_ENVIRONMENT` | `development` | environment tag on Honeybadger reports; the production env file sets `production` |
| `EMAIL_HOST` / `EMAIL_PORT` | `mail` / `1025` | SMTP (the mailcatcher service in dev) |
| `EMAIL_FROM` | `cervo@localhost` | From address on outgoing mail |
| `EMAIL_USER` / `EMAIL_PASSWORD` | *(empty)* | set for a real SMTP provider — switches `mail.send` to STARTTLS + login (port 587 shape) |

Auth has no knobs: token and code lifetimes are private constants in
`cervo.auth.service`, identical everywhere by design.

## Layout

- `src/cervo/server.py` — the FastMCP instance (`app`, constructed with
  `auth=CervoOAuthProvider()`) and all tool definitions. Tools are plain
  text/structured tools — no MCP apps, no UI resources. There is no sign-in
  tool: every MCP request carries a Bearer token or is refused with a 401 at
  the transport, and tools resolve the owner from the token (`_owner`).
- `src/cervo/web/` — the public website: pages built from FastHTML fasttags on
  the design system (`design-system/`), registered on the FastMCP app as custom
  HTTP routes (`web.register(app)` at the bottom of server.py). FastMCP appends
  custom routes after `/mcp`, so they cannot shadow it; among the pages the
  catch-all 404 route must stay registered last. The same components are the
  single source for a site's default page (`web.default_page`), which the
  worker renders and writes at deploy time. The docs page's illustrations
  (`web/figures.py`) are inline SVG drawn from the same theme tokens — no
  binary assets, no external requests, and they follow the light/dark toggle.
- `src/cervo/worker.py` — the job worker: polls for due jobs, dispatches them by
  kind (`_HANDLERS`), reaps timed-out ones. Entry point `cervo-worker`.
- `src/cervo/config.py` — settings (see above)
- `src/cervo/user/`, `src/cervo/website/`, `src/cervo/auth/`, `src/cervo/job/` —
  one package per domain (see below)
- `src/cervo/schema.py` — `create_tables()`, the one place that knows every table
- `src/cervo/db.py` — `connect()`, the connection context manager (WAL, busy
  timeout, `IMMEDIATE` transactions — what lets several processes share the
  file), and `transact()`, the only way async code runs one: the whole
  transaction goes to a worker thread, so the event loop never blocks on
  SQLite (or on the SMTP send inside `auth.send_code`). Sync `def` tools
  need no wrapper — FastMCP already runs those in its threadpool
- `src/cervo/errors.py` — `AppError`, the base for failures the user should read
- `src/cervo/mail.py` — sending mail over SMTP (the sign-in codes)
- `src/cervo/monitoring.py` — reporting to Honeybadger, production only: the
  ASGI wrap and MCP middleware for the server's errors, `report`/`event` for
  the worker's, and `setup`. Everything is a no-op without the API key.
- `src/cervo/caddy.py` — rendering the Caddyfile from the database and reloading
  caddy over its admin API
- `src/cervo/templates/` — jinja2 templates: the Caddyfile, plus the theme
  token block (`_tokens.css`) the website's pages inline
- `src/cervo/__init__.py` — `main()` entrypoint (the `app` service): creates
  the tables, then hands uvicorn the `cervo.asgi:application` import string
- `src/cervo/asgi.py` — the ASGI app uvicorn's workers import; built with
  `stateless_http=True`, the setting that makes multiple workers safe
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

When the inventory carries `honeybadger_api_key`, the playbook also turns on
observability: the key lands in the environment file (enabling error
reporting and Insights in the services), vector is installed to ship the
cervo units' journald output to Honeybadger Insights
(`deploy/templates/vector.yaml.j2`), and the deployed revision — `image_tag`
resolved to a full commit sha, honest for rollbacks too — is reported to
Honeybadger's deploys API after the services restart.

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
and payload serialization never leave its `_dao`. A kind can be declared
one-at-a-time with `job.serialize(kind)` — enforced in the claim statement
itself, so it holds across any number of worker processes; the domain owning
the kind declares it at import time. `job.serialize(kind, group)` puts several
kinds in one group so they take turns with *each other*, not just with their
own kind — how the three Caddyfile kinds are kept from ever running two at
once. A claimed job also carries a `claims` generation, bumped on every claim,
that its worker must still hold to finalize it — so a job reaped after a
timeout and reclaimed by another worker cannot be mutated by the first.

## Authentication

cervo is its own OAuth 2.1 authorization server; connecting it as a claude.ai
custom connector (authentication: required) is the only sign-in. FastMCP
mounts the endpoints (`/.well-known/*`, `/authorize`, `/token`, `/register`,
`/revoke`) from `auth/provider.py`'s `CervoOAuthProvider`; the metadata is
rebuilt there to advertise CIMD, so Claude's "hosted client metadata"
(client_id = an Anthropic-hosted URL) works as well as plain DCR.

For now cervo is Claude-only, enforced in `CervoOAuthProvider` so it cannot
be turned into a confused-deputy for an attacker's client: `register_client`
accepts a self-registering (DCR) client only when every `redirect_uri` is a
loopback callback (Claude Code's ephemeral `http://localhost:PORT/callback`)
or `https://claude.ai/...`, and `get_client` honours a CIMD `client_id` only
when its document host is `claude.ai`. Everything else is refused — a loopback
code only ever reaches the user's own machine, and no attacker controls
`claude.ai`. Admitting another provider (ChatGPT, Gemini) later is a matter of
adding its callback and CIMD hosts to the allowlists at the top of
`auth/provider.py`.

`/authorize` parks the request as a transaction and redirects the browser to
`/verify` (`web/verify.py`): the user enters an email, a six-digit code is
mailed, and typing it back ends with a redirect to Claude's callback carrying
a single-use authorization code. `/token` (PKCE-verified by the SDK)
exchanges it for an access token (1 h) plus a rotating refresh token — both
stored hashed, keyed to the `user` row the verified email resolved to. Tools
read the identity per request via `get_access_token()`: the subject is the
user id, the email a claim. Nothing about auth is per-conversation anymore —
a connector stays signed in as long as Claude keeps refreshing.

The email page states that connecting means agreeing to the terms of service
and privacy policy (`/terms`, `/privacy`); connector setup is documented for
users on the public `/docs` page and for operators in the README's
"Connecting from claude.ai" section.

## Jobs and deployment

Creating a website inserts the row and enqueues the first job of the deploy
chain — the MCP server never provisions anything itself. A deployment is three
chained jobs, and the worker enqueues each next one in the same transaction
that marks its predecessor done: `website.provision` creates `DATA_DIR/{slug}/`
and writes the default `index.html` (only if missing — an owner's replaced
files are never clobbered), `website.configure` regenerates the whole Caddyfile
from the database, and `website.activate` POSTs it to caddy's `/load` admin
endpoint. Every step is idempotent, so retrying is always safe — and only the
failed step retries, not the whole chain. The steps that rewrite or reload
the shared Caddyfile (`website.configure`, `website.activate`,
`website.delete`) are serialized as one group — at most one of the three runs
at a time, however many workers there are — so a delete cannot render its
stale snapshot over a configure that just added another site, while other
kinds keep flowing around them.

Because the deployment is now stepwise, a site also reports `step`,
`steps_done`, and `steps_total`, and `create_website` streams real-time
progress: when the client sent a `progressToken`, the tool follows the chain
with `ctx.report_progress` (one notification per step) and returns the site
already `live` (or `failed`); without a token it returns immediately as
status `pending`, and `list_websites` follows instead.

Job lifecycle: `pending → running → done`, or on failure back to `pending` with
`attempts + 1` and a retry delay, until `failed` for good after `_MAX_ATTEMPTS`.
A running job that outlives its `timeout` is reaped — counted as a failed attempt
and made pending again — which is also the crash recovery: a worker killed
mid-job needs no shutdown protocol. At startup the worker also "heals": it
renders and reloads the Caddyfile even with no jobs queued, so a fresh checkout
or restored data directory starts serving immediately.

Writing a file (`write_file`, owner-only) reuses the same machinery as its
own chain: the tool fast-fails anything structurally wrong — only relative
lowercase `.html`/`.css` paths, no `..`/`\`/leading `/`, at most 1 MiB —
and queues `website.validate_file` (content sanity: real UTF-8 text that
survives the stdlib HTML tokenizer or a small CSS scanner; nothing is
executed) followed by `website.write_file`, which re-checks the path and
that the site still exists before writing (`website.file_target` is the one
safe join, used by both server and worker). Validation failures raise
`job.PermanentError` — failed for good, no retries. No caddy step: the file
server picks new files up immediately. A future virus-scan step is one more
entry in `FILE_CHAIN`.

Deleting a file (`delete_file`, owner-only) is a single-job chain
(`website.delete_file`): the tool checks ownership, the path, and that the
file actually exists, then queues the removal. The payload carries the
owner's id, and the worker re-checks the site against it before touching
disk — a slug freed and re-taken meanwhile makes the job a
`job.PermanentError`, never a deleted file for the new owner. Folders the
deletion empties are pruned; deleting index.html puts the default landing
page back in its place, so a site never loses its root. No caddy step, same
as writing.

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
OAuth metadata advertising CIMD, the whole browser sign-in with the code read
from mailcatcher's API, the MCP endpoint refusing tokenless requests, slug
validation and ownership rules, and a site created, polled to `live`, and its
page actually fetched through caddy. OAuth issuers must be https or
localhost, so the test stack keeps `DOMAIN=localhost` and the smoke runner
joins caddy's network namespace (`network_mode: service:caddy`) — and caddy
is seeded with a stub Caddyfile there so it never crash-loops under the
runner's feet. The file is intentionally named
so a plain `pytest` run skips it (it needs the stack up); sites are
fetched through the front door with a Host header.

The unit suite is hermetic on top of the stack isolation (see below), so
`uv run pytest` on the host works too — CI (`.github/workflows/test.yml`)
lints with uv, then runs `bin/test` and `bin/smoke` on push to `main` and on
every pull request.

Tests never touch development data or services: autouse fixtures in
`tests/conftest.py` repoint `config.DATA_DIR` and `config.DATABASE_PATH` at a
per-test `tmp_path` (creating the tables there), replace `mail.send` with a
capture list, replace `caddy.reload` the same way, and capture the
Honeybadger client's sends (`reports`, `insights`) while setting a fake API
key, so the real reporting paths run without a byte leaving the process. All
four are autouse — a test cannot escape them by forgetting a fixture — and
`tests/test_isolation.py` asserts the guarantees hold.

Write tests against the MCP tools rather than the services. Auth lives in
the HTTP layer, so the suite runs MCP **over the ASGI app**: `chat(email)`
signs in through the real OAuth flow (`Flow` in `tests/conftest.py` — DCR,
authorize, verify pages with the code read from the `mailbox` fixture, PKCE
token exchange) and yields a client whose requests carry the Bearer token; an
in-process client would silently skip auth, so nothing uses one. `serving()`
yields a plain HTTP client against the same app for probing the OAuth
endpoints themselves (`tests/test_auth.py`). The website's pages are tested
through a starlette `TestClient` over `app.http_app()` (`tests/test_web.py`).
The worker never runs as a process in tests — call `worker.run_once()` (or the
`deploy()` helper) for deterministic deployments.

Note `tests/__init__.py` is required: a transitive dependency (`caio`) installs a
top-level `tests` package that otherwise shadows this one.

## Testing with Claude Code

The server is registered as a project MCP server in `.mcp.json` at
`http://localhost/mcp` — through caddy, like production. When the stack is up, its
tools are available directly in the chat — the primary way to test is to just call
them and check the results.

- The stack must already be running (`docker compose up -d`) **before starting the Claude Code session** — Claude Code connects to MCP servers at session startup. If tool calls fail to connect, check `docker compose ps` (on a fresh checkout, give the worker a moment to render the Caddyfile so caddy stays up), then run `/mcp` to connect.
- Connecting requires OAuth: `/mcp` opens the browser on cervo's sign-in page. Enter any address and read the six-digit code from mailcatcher at http://localhost:1080 — no real mail is sent in development. The connection then stays signed in across restarts of the stack (tokens live in the database volume).
- Claude Code never reconnects automatically: whenever you change the MCP server code, `docker compose restart app` and run `/mcp` to reconnect. This is mandatory when tool schemas change (names, parameters, docstrings), since tool definitions are cached from the initial handshake; if only a tool's body changed, restarting the service is enough — the next call reaches the fresh process as long as the schema still matches. Worker-side changes (deployments) need only `docker compose restart worker`.
- For checks the MCP connection can't cover (error cases, raw protocol), use a throwaway `fastmcp.Client` script — pass `auth="oauth"` to run the same browser sign-in, or drive the flow by hand the way `tests/smoke.py` does:

```python
import asyncio
from fastmcp import Client


async def main():
    async with Client("http://localhost/mcp", auth="oauth") as client:
        print(await client.list_tools())


asyncio.run(main())
```
