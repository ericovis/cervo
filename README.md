# Cervo

A demo app for managing static website hosting on a shared VPS. It's built as an MCP server ([FastMCP](https://gofastmcp.com), served over HTTP), so the hosting is managed by chatting with an AI: in development, [Claude Code](https://claude.com/claude-code) acts as the chat interface for exercising and testing the server's tools.

## Quick start

Prerequisite: [Docker](https://www.docker.com/) (with Compose).

```bash
bin/dev    # docker compose up -d: app, worker, caddy, mail
```

Caddy fronts everything on port 80: the MCP server at `http://localhost/mcp`, and each created site at `http://{slug}.localhost`.

Then open Claude Code in this repo — the server is pre-registered in `.mcp.json`, so its tools become available directly in the chat. Connecting runs cervo's OAuth sign-in in the browser: enter an email and type back the code that lands in [mailcatcher](http://localhost:1080) (no real mail is sent in development). Start the stack *before* opening the Claude Code session (connections are made at startup), and run `/mcp` to reconnect whenever you change the MCP server code (`docker compose restart app`) — Claude Code doesn't reconnect automatically.

On claude.ai, add cervo as a custom connector pointing at `https://{your-domain}/mcp` with **authentication: always required** — "Use Anthropic's hosted client metadata" works out of the box (cervo advertises CIMD) and is the recommended option.

Development works with zero configuration; settings can be overridden via a `.env` file (see the [configuration table](CLAUDE.md#configuration)).

## Deploying

Production is the same image on a VPS, run by rootful [podman
quadlets](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
(podman ≥ 4.4 — Debian 13). Deploys run from your machine: `bin/deploy`
builds the image for `linux/amd64`, pushes it to Docker Hub tagged with the
git sha, and runs the ansible playbook in `deploy/`, which writes the
quadlet units and environment, pulls the image, and restarts the services —
restarting the worker re-renders the Caddyfile, so config changes always
land. Secrets never live in the repo: the Docker Hub token and SMTP
password are read from 1Password by the `op` CLI at deploy time.

One-time setup:

1. DNS: an `A` record for the apex and a wildcard `A *` record, both to the
   server's IP — sites live at `https://{slug}.{domain}`.
2. 1Password items (vault `cervo`): `docker-hub` with a `token` field, and
   `smtp` with a `password` field (the `op://` paths are inventory vars, so
   any layout works).
3. Create `deploy/inventory.yml` (gitignored — every deploy setting lives
   here, nothing is hardcoded):

   ```yaml
   cervo:
     hosts:
       cervo-vps:
         ansible_host: your.server.ip
         ansible_port: 22
         ansible_user: debian
     vars:
       image_repo: docker.io/you/cervo
       dockerhub_user: you
       op_dockerhub_token: op://cervo/docker-hub/token
       domain: example.com
       acme_email: you@example.com
       email_host: smtp.example.com # port-587 STARTTLS provider
       email_port: 587
       email_user: your-smtp-user
       email_from: cervo@example.com # a sender your provider verified
       op_smtp_password: op://cervo/smtp/password
   ```
4. On your machine: `ansible` and `op` installed, `op` signed in, docker
   logged out is fine — `bin/deploy` logs in itself.

Then every deploy — first and later alike — is:

```bash
bin/deploy
```

With `SCHEME=https` (set by the playbook) caddy obtains a certificate per
hostname from Let's Encrypt and redirects plain http; the first request to
a fresh site waits a few seconds while its certificate is issued.
Certificates persist in the `caddy-data` volume, so redeploys never
re-issue them.

## Documentation

Detailed information lives in [CLAUDE.md](CLAUDE.md) (also loaded by Claude Code as project context):

- [Configuration](CLAUDE.md#configuration) — all settings and their defaults
- [Layout](CLAUDE.md#layout) — where things live in the codebase
- [Testing with Claude Code](CLAUDE.md#testing-with-claude-code) — the development/testing workflow
