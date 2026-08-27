"""Fixtures that keep tests off the development data, SMTP, and caddy.

All three guards are autouse, so a test cannot reach real data, a real mail
server, or caddy's admin API even by forgetting to ask for a fixture.
"""

import re
from dataclasses import dataclass

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult

from cervo import caddy, config, mail, server, worker
from cervo.schema import create_tables
from cervo.server import app

OWNER = "owner@example.com"


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


@dataclass
class Message:
    to: str
    subject: str
    body: str

    @property
    def code(self) -> str:
        """The six-digit code in the body, so tests read mail like a user."""
        match = re.search(r"code is: (\d{6})", self.body)
        assert match, f"no code in email body:\n{self.body}"
        return match.group(1)


class Mailbox(list):
    """Everything the app tried to send during a test."""

    @property
    def last(self) -> Message:
        assert self, "no email was sent"
        return self[-1]

    @property
    def last_code(self) -> str:
        return self.last.code


@pytest.fixture(autouse=True)
def mailbox(monkeypatch) -> Mailbox:
    """Capture outgoing mail instead of talking to mailcatcher."""
    sent = Mailbox()

    def fake_send(to: str, subject: str, body: str) -> None:
        sent.append(Message(to=to, subject=subject, body=body))

    monkeypatch.setattr(mail, "send", fake_send)
    return sent


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


def chat(confirms: str | None = None, *, action: str = "accept") -> Client:
    """A client whose connection is one conversation, with a scripted human.

    By default the human accepts whatever address the tool proposed, which is
    the ordinary case. Pass ``confirms`` to have them type a different one, so
    a test can model correcting the agent's guess, and ``action`` to have them
    dismiss the form.
    """

    async def handler(message, response_type, params, context):
        if action != "accept":
            return ElicitResult(action=action)
        proposed = params.requestedSchema["properties"]["email"]["default"]
        return response_type(email=confirms or proposed)

    return Client(app, elicitation_handler=handler)


async def call(client: Client, tool: str, **arguments) -> str:
    """Call a tool and return its text content."""
    result = await client.call_tool(tool, arguments)
    return result.content[0].text


async def sign_in(client: Client, mailbox: Mailbox, email: str = OWNER) -> str:
    """Run the whole sign-in handshake the way an agent would."""
    await call(client, "authenticate", email=email)
    return await call(client, "confirm_authentication", code=mailbox.last_code)
