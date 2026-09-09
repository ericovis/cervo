"""Fixtures keeping tests off the development data, SMTP, caddy — and Honeybadger.

All four guards are autouse, so a test cannot reach real data, a real mail
server, caddy's admin API, or Honeybadger even by forgetting to ask for a
fixture. Caddy's is a fake admin API rather than a black hole
(:class:`FakeCaddy`), so what the worker publishes can be asserted on.

Auth is enforced at the HTTP layer, so tests talk to the server the way
Claude does: over its ASGI app, signing in through the real OAuth flow
(:class:`Flow`) and carrying the Bearer token on every MCP request. An
in-process client would skip all of that, which is exactly what must not
happen silently.
"""

import base64
import hashlib
import re
import secrets
from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from honeybadger import honeybadger

from cervo import caddy, config, mail, server, worker
from cervo.schema import create_tables
from cervo.server import app

OWNER = "owner@example.com"

# Where the scripted client asks to be called back — the shape of Claude
# Code's loopback redirect.
CALLBACK = "http://localhost:33418/callback"

_ORIGIN = "http://localhost"

# The fallback CA the Caddyfile adapter gives every certificate — asserted
# on, so a policy cervo writes cannot quietly become worse than the one the
# static file writes for cervo's own hostname.
ZEROSSL = "https://acme.zerossl.com/v2/DV90"


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
    """Pin the domain and scheme, so URL assertions hold wherever it runs."""
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


# The current test's captured mail, so the sign-in flow can read codes the
# way a user reads their inbox. Set by the autouse fixture below.
_inbox: Mailbox | None = None


@pytest.fixture(autouse=True)
def mailbox(monkeypatch) -> Mailbox:
    """Capture outgoing mail instead of talking to mailcatcher."""
    global _inbox
    sent = Mailbox()

    def fake_send(to: str, subject: str, body: str) -> None:
        sent.append(Message(to=to, subject=subject, body=body))

    monkeypatch.setattr(mail, "send", fake_send)
    _inbox = sent
    return sent


@pytest.fixture(autouse=True)
def reports(monkeypatch) -> list:
    """Capture Honeybadger error reports instead of talking to its API.

    An API key is set so the real reporting paths run under test; what is
    replaced is the client's send. A key in the host environment therefore
    cannot leak reports out of a test run either — and tests get to assert
    exactly what would have been sent.
    """
    monkeypatch.setattr(config, "HONEYBADGER_API_KEY", "hbp_test")
    captured = []

    def fake_notify(**kwargs) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(honeybadger, "notify", fake_notify)
    return captured


@pytest.fixture(autouse=True)
def insights(monkeypatch) -> list:
    """Capture Insights events the same way `reports` captures errors."""
    captured = []

    def fake_event(event_type, data) -> None:
        captured.append((event_type, data))

    monkeypatch.setattr(honeybadger, "event", fake_event)
    return captured


@pytest.fixture(autouse=True)
def no_follow(monkeypatch):
    """create_website hands the site back at once: no worker process runs.

    Clients send a progress token by default, and the tool would otherwise
    watch the deployment for a while. It still sends one progress report;
    the streaming test widens the window itself to watch a whole chain.
    """
    monkeypatch.setattr(server, "_FOLLOW_FOR", 0)


class FakeCaddy:
    """Caddy's admin API in memory: the config, and how it answers.

    Seeded with exactly what the static ``caddy/Caddyfile`` adapts to, and
    as strict as the real thing about what it refuses — a 404 for an
    unknown id, an "invalid traversal path" for a write into a parent that
    does not exist, and the same for *reading* through one, which only a
    missing last segment survives — so code that drifts from caddy's
    semantics fails here rather than in production. Every call is recorded
    in ``calls``, which is how a test asserts that nothing was written.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.broken = False  # set to make every call fail, as a down caddy does
        self.http()

    def http(self) -> None:
        """Seed the config the Caddyfile adapts to under SCHEME=http."""
        self.config = {
            "admin": {"listen": "0.0.0.0:2019"},
            "apps": {
                "http": {
                    "servers": {"srv0": {"listen": [":80"], "routes": [_apex_route()]}}
                }
            },
        }

    def https(self, email: str | None = "certs@example.com") -> None:
        """Seed the config it adapts to under SCHEME=https.

        There is an automation policy for cervo's own hostname either way —
        with no ``ACME_EMAIL`` the issuers simply carry no email. Note the
        policy names its subject: cervo's own hostname, not a catch-all,
        which is what lets sites append theirs. A caddy holding no ``tls``
        app at all is not a shape this Caddyfile produces under https; a
        test that wants it deletes the app itself.
        """
        self.http()
        self.server["listen"] = [":443"]
        issuers = [{"module": "acme"}]  # no contact: the adapter names no CA either
        if email is not None:
            issuers = [
                {"module": "acme", "email": email},
                {"module": "acme", "ca": ZEROSSL, "email": email},
            ]
        self.config["apps"]["tls"] = {
            "automation": {
                "policies": [{"subjects": [config.DOMAIN], "issuers": issuers}]
            }
        }

    @property
    def server(self) -> dict:
        """The one server the adapter produced."""
        return self.config["apps"]["http"]["servers"]["srv0"]

    @property
    def routes(self) -> list:
        return self.server["routes"]

    @property
    def policies(self) -> list:
        tls = self.config["apps"].get("tls", {})
        return tls.get("automation", {}).get("policies", [])

    def object(self, identity: str) -> dict | None:
        """The object carrying this ``@id``, the way caddy finds one."""
        found = self._locate(identity)
        return found[0][found[1]] if found else None

    @property
    def writes(self) -> list[tuple[str, str]]:
        """Only the calls that changed something — reads are free."""
        return [call for call in self.calls if call[0] != "GET"]

    def api(self, method: str, path: str, body=None) -> tuple[int, object]:
        """What :func:`cervo.caddy._api` does, without a socket."""
        self.calls.append((method, path))
        if self.broken:
            raise RuntimeError("caddy is down")
        if path.startswith("/id/"):
            return self._by_id(method, path[len("/id/") :], body)
        if path == "/config" or path.startswith("/config/"):
            return self._by_path(method, path[len("/config") :], body)
        return 404, {"error": f"unknown endpoint {path}"}

    def _by_id(self, method: str, identity: str, body) -> tuple[int, object]:
        found = self._locate(identity)
        if found is None:
            return 404, {"error": f"unknown object ID '{identity}'"}
        holder, key = found
        if method == "GET":
            return 200, deepcopy(holder[key])
        if method == "PATCH":  # replaces the object; the id is the body's job
            holder[key] = deepcopy(body)
            return 200, None
        if method == "DELETE":
            del holder[key]
            return 200, None
        return 405, {"error": f"method {method} not allowed"}

    def _locate(self, identity: str):
        """The container and key of the object with this ``@id``."""
        stack: list = [self.config]
        while stack:
            current = stack.pop()
            entries = (
                current.items()
                if isinstance(current, dict)
                else enumerate(current)
                if isinstance(current, list)
                else ()
            )
            for key, value in entries:
                if isinstance(value, dict) and value.get("@id") == identity:
                    return current, key
                if isinstance(value, (dict, list)):
                    stack.append(value)
        return None

    def _by_path(self, method: str, path: str, body) -> tuple[int, object]:
        segments = [part for part in path.split("/") if part]
        if method == "GET":
            # Only a missing *last* segment reads as null; a missing one on
            # the way there is a refusal, which is what makes reading deep
            # into a config that may not have the path a mistake.
            if segments and self._walk(segments[:-1]) is None:
                return 400, {"error": f"invalid traversal path at: config{path}"}
            return 200, deepcopy(self._walk(segments))
        if not segments:
            return 400, {"error": "cannot write the whole config here"}
        key = segments[-1]
        if method == "PUT":  # creates the whole path it is given
            parent = self._make(segments[:-1])
            if parent is None:
                return self._untraversable(path)
            if isinstance(parent, list):  # an index: inserts, never replaces
                parent.insert(int(key), deepcopy(body))
                return 200, None
            if key in parent:
                return 409, {"error": f"[/config{path}] key already exists: {key}"}
            parent[key] = deepcopy(body)
            return 200, None
        parent = self._walk(segments[:-1])
        if parent is None:
            return self._untraversable(path)
        target = self._child(parent, key)
        if method == "POST":
            if isinstance(target, list):  # POST appends to an array
                target.append(deepcopy(body))
                return 200, None
            if target is None:  # caddy creates no intermediate objects here
                return self._untraversable(path)
            return 500, {"error": f"cannot append to {path}"}
        if method == "PATCH":  # replaces an existing value
            if target is None:
                return self._untraversable(path)
            parent[int(key) if isinstance(parent, list) else key] = deepcopy(body)
            return 200, None
        if method == "DELETE":
            if target is None:
                return 404, {"error": f"[/config{path}] key does not exist: {key}"}
            del parent[int(key) if isinstance(parent, list) else key]
            return 200, None
        return 405, {"error": f"method {method} not allowed"}

    @staticmethod
    def _untraversable(path: str) -> tuple[int, object]:
        """How caddy refuses a *write* through a path that is not there."""
        return 500, {"error": f"invalid traversal path at: config{path}"}

    def _walk(self, segments: list[str]):
        """The value at this config path, or None if nothing is there."""
        current = self.config
        for segment in segments:
            current = self._child(current, segment)
            if current is None:
                return None
        return current

    def _make(self, segments: list[str]):
        """The value at this path, creating what is missing on the way.

        What a PUT does: unlike an append, it brings the whole path with it.
        """
        current = self.config
        for segment in segments:
            child = self._child(current, segment)
            if child is None:
                if not isinstance(current, dict):
                    return None
                child = current[segment] = {}
            current = child
        return current

    @staticmethod
    def _child(current, key: str):
        if isinstance(current, dict):
            return current.get(key)
        if isinstance(current, list) and key.isdigit() and int(key) < len(current):
            return current[int(key)]
        return None


def _apex_route() -> dict:
    """cervo's own route, as the static Caddyfile's reverse_proxy adapts.

    It carries no ``@id``: it is not cervo's to manage at runtime, and every
    reconciliation must leave it exactly where it is.
    """
    return {
        "match": [{"host": [config.DOMAIN]}],
        "handle": [
            {
                "handler": "subroute",
                "routes": [
                    {
                        "handle": [
                            {
                                "handler": "reverse_proxy",
                                "upstreams": [{"dial": config.MCP_UPSTREAM}],
                            }
                        ]
                    }
                ],
            }
        ],
        "terminal": True,
    }


@pytest.fixture(autouse=True)
def caddy_api(monkeypatch) -> FakeCaddy:
    """Answer caddy's admin API from memory instead of over the network.

    The whole module goes through ``caddy._api``, so replacing that one
    function is enough to keep every test off the real admin API — and
    gives tests caddy's config to assert on.
    """
    fake = FakeCaddy()

    def fake_api(method: str, path: str, body=None) -> tuple[int, object]:
        return fake.api(method, path, body)

    monkeypatch.setattr(caddy, "_api", fake_api)
    return fake


def deploy() -> int:
    """Run every due job the way the worker service would, deterministically.

    Returns how many jobs ran, so a test can assert there was (or was not)
    work to do.
    """
    ran = 0
    while worker.run_once():
        ran += 1
    return ran


class Flow:
    """The OAuth dance against the server's ASGI app, step by step.

    Exactly what Claude does when the connector is added: register as a
    client, open /authorize, land on the verification pages, and exchange
    the resulting code — with PKCE throughout. Tests drive single steps to
    probe them; ``sign_in`` runs the whole thing.
    """

    def __init__(self, web: httpx.AsyncClient):
        self.web = web
        self.verifier = secrets.token_urlsafe(43)
        digest = hashlib.sha256(self.verifier.encode()).digest()
        self.challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        self.state = secrets.token_urlsafe(8)
        self.client_id: str | None = None
        self.txn: str | None = None

    async def register(self) -> str:
        response = await self.web.post(
            "/register",
            json={
                "redirect_uris": [CALLBACK],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_name": "tests",
            },
        )
        assert response.status_code == 201, response.text
        self.client_id = response.json()["client_id"]
        return self.client_id

    async def authorize(self) -> str:
        """Start the flow; returns the txn id the browser lands on."""
        if self.client_id is None:
            await self.register()
        response = await self.web.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": CALLBACK,
                "state": self.state,
                "code_challenge": self.challenge,
                "code_challenge_method": "S256",
            },
        )
        assert response.status_code == 302, response.text
        location = response.headers["location"]
        self.txn = parse_qs(urlparse(location).query)["txn"][0]
        return self.txn

    async def submit_email(self, email: str, accept: bool = True) -> httpx.Response:
        data = {"txn": self.txn, "email": email}
        if accept:  # the consent tick box, as a browser would send it
            data["accept"] = "yes"
        return await self.web.post("/verify/email", data=data)

    async def submit_code(self, code: str) -> httpx.Response:
        return await self.web.post("/verify/code", data={"txn": self.txn, "code": code})

    async def exchange(self, code: str) -> httpx.Response:
        return await self.web.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": CALLBACK,
                "client_id": self.client_id,
                "code_verifier": self.verifier,
            },
        )

    async def refresh(self, refresh_token: str) -> httpx.Response:
        return await self.web.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
            },
        )

    async def sign_in(self, email: str) -> dict:
        """The whole handshake; returns the token response's JSON."""
        await self.authorize()
        response = await self.submit_email(email)
        assert response.status_code == 303, response.text
        assert _inbox is not None
        response = await self.submit_code(_inbox.last_code)
        assert response.status_code == 302, response.text
        query = parse_qs(urlparse(response.headers["location"]).query)
        assert query["state"] == [self.state]
        response = await self.exchange(query["code"][0])
        assert response.status_code == 200, response.text
        return response.json()


class chat:
    """A signed-in MCP conversation against the server, over its ASGI app.

    Entering runs the real OAuth flow for ``email`` and yields a client
    whose every request carries the Bearer token — one conversation, one
    identity, the way Claude holds a connector.
    """

    def __init__(self, email: str = OWNER):
        self._email = email

    async def __aenter__(self) -> Client:
        self._http = app.http_app(stateless_http=True)
        self._lifespan = self._http.router.lifespan_context(self._http)
        await self._lifespan.__aenter__()
        async with web_client(self._http) as web:
            tokens = await Flow(web).sign_in(self._email)
        self._client = Client(
            StreamableHttpTransport(
                f"{_ORIGIN}/mcp", httpx_client_factory=self._factory
            ),
            auth=tokens["access_token"],
        )
        return await self._client.__aenter__()

    async def __aexit__(self, *exc) -> None:
        try:
            await self._client.__aexit__(*exc)
        finally:
            await self._lifespan.__aexit__(*exc)

    def _factory(
        self, headers=None, auth=None, follow_redirects=True, timeout=None, **kwargs
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self._http),
            base_url=_ORIGIN,
            headers=headers,
            auth=auth,
            follow_redirects=follow_redirects,
            **({"timeout": timeout} if timeout else {}),
        )


def web_client(http_app) -> httpx.AsyncClient:
    """A browser for the ASGI app — redirects left visible for inspection."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=http_app),
        base_url=_ORIGIN,
        follow_redirects=False,
    )


class serving:
    """The server's ASGI app with its lifespan running, for raw HTTP tests."""

    async def __aenter__(self):
        self._http = app.http_app(stateless_http=True)
        self._lifespan = self._http.router.lifespan_context(self._http)
        await self._lifespan.__aenter__()
        self._web = web_client(self._http)
        return await self._web.__aenter__()

    async def __aexit__(self, *exc) -> None:
        try:
            await self._web.__aexit__(*exc)
        finally:
            await self._lifespan.__aexit__(*exc)


async def call(client: Client, tool: str, **arguments) -> str:
    """Call a tool and return its text content."""
    result = await client.call_tool(tool, arguments)
    return result.content[0].text
