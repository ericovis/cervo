"""Fixtures that keep tests off the development data and caddy.

Both guards are autouse, so a test cannot reach real data or caddy's admin
API even by forgetting to ask for a fixture.
"""

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from mcp.types import Implementation

from cervo import caddy, config, server, worker
from cervo.schema import create_tables
from cervo.server import app

OWNER = "owner@example.com"

# What a real Claude client sends in the initialize handshake; the server
# only signs in clients whose name says Claude.
CLAUDE = Implementation(name="claude-code", version="0.0.0-test")


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Point every path setting at a throwaway directory, per test.

    ``connect()`` reads ``config.DATABASE_PATH`` on each call, so patching the
    attribute is enough — no environment variable or re-import needed.
    """
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DATABASE_PATH", data / "cervo.db")
    create_tables()
    return data


@pytest.fixture(autouse=True)
def domain(monkeypatch):
    """Pin the domain and scheme, so URL assertions hold wherever it runs.

    The test stack sets DOMAIN=caddy for the smoke test's sake; the unit
    suite never talks to the network, so it always sees the defaults.
    """
    monkeypatch.setattr(config, "DOMAIN", "localhost")
    monkeypatch.setattr(config, "SCHEME", "http")


@pytest.fixture(autouse=True)
def no_follow(monkeypatch):
    """create_website hands the site back at once: no worker process runs.

    Clients send a progress token by default, and the tool would otherwise
    watch the deployment for a while. It still sends one progress report;
    the streaming test widens the window itself to watch a whole chain.
    """
    monkeypatch.setattr(server, "_FOLLOW_FOR", 0)


@pytest.fixture(autouse=True)
def caddy_reloads(monkeypatch) -> list:
    """Capture caddy reloads instead of talking to its admin API.

    Only the network call is replaced: rendering the Caddyfile writes under
    the per-test data directory, so tests can assert on the real file.
    """
    reloads = []

    def fake_reload() -> None:
        reloads.append(True)

    monkeypatch.setattr(caddy, "reload", fake_reload)
    return reloads


def deploy() -> int:
    """Run every due job the way the worker service would, deterministically.

    Returns how many jobs ran, so a test can assert there was (or was not)
    work to do.
    """
    ran = 0
    while worker.run_once():
        ran += 1
    return ran


def chat(
    confirms: str | None = None,
    *,
    action: str = "accept",
    client_info: Implementation = CLAUDE,
) -> Client:
    """A client whose connection is one conversation, with a scripted human.

    By default the human accepts whatever address the tool proposed, which is
    the ordinary case. Pass ``confirms`` to have them type a different one, so
    a test can model correcting the agent's guess, and ``action`` to have them
    dismiss the form. The client introduces itself as Claude unless a test
    passes another ``client_info`` to model a stranger.
    """

    async def handler(message, response_type, params, context):
        if action != "accept":
            return ElicitResult(action=action)
        proposed = params.requestedSchema["properties"]["email"]["default"]
        return response_type(email=confirms or proposed)

    return Client(app, elicitation_handler=handler, client_info=client_info)


async def call(client: Client, tool: str, **arguments) -> str:
    """Call a tool and return its text content."""
    result = await client.call_tool(tool, arguments)
    return result.content[0].text


async def sign_in(client: Client, email: str = OWNER) -> str:
    """Sign the conversation in the way an agent would."""
    return await call(client, "authenticate", email=email)
