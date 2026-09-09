"""Caddy, the front door: cervo's whole config, written from the database.

Caddy boots with no config at all (``caddy run --resume``, and no
``$XDG_CONFIG_HOME/caddy/autosave.json`` to resume the very first time — the
file appears only once a config has been loaded), and everything it serves is
put there over its admin API: cervo's own reverse proxy and one route per
hosted site. The database — plus ``DOMAIN``, ``SCHEME``, ``ACME_EMAIL``,
``DATA_DIR`` and ``MCP_UPSTREAM`` — is the only source of truth; caddy's
running config is a cache of it, the autosave is what makes that cache
survive a restart, and :func:`sync` is the one writer that rebuilds it.

So there is exactly one shape to reason about: :func:`apps` is a pure function
of the sites, and a reconciliation either finds caddy holding precisely that
(one read, no write) or replaces the whole tree in a single call. Every call
goes through :func:`_api`, the one seam tests replace — nothing else in this
module opens a socket.
"""

import json
import urllib.error
import urllib.request
from typing import Any

from cervo import config
from cervo.website.types import Route

_TIMEOUT = 10  # seconds; the admin API is a container away, on a socket

_APPS = "/config/apps"  # the whole tree cervo owns, written in one call

# The ids cervo puts on the objects it writes. Nothing reads them back — the
# tree is compared whole — but they are what an operator (and the smoke
# tests) look one up by: GET /id/site:{slug}.
_APEX_ID = "cervo"
_APEX_POLICY_ID = "cervo:tls"
_ROUTE_ID = "site:"
_POLICY_ID = "tls:"

# The second CA the Caddyfile adapter used to give every certificate, cervo's
# own hostname included. Kept for both: an owner whose certificate Let's
# Encrypt cannot issue (an outage, a rate limit) is no worse off than they
# were when a rendered Caddyfile said `tls {email}`.
_FALLBACK_CA = "https://acme.zerossl.com/v2/DV90"


class CaddyError(RuntimeError):
    """Caddy refused a request — raised with its own explanation."""


def apps(sites: list[Route]) -> dict[str, Any]:
    """Everything caddy should be running, for exactly these sites.

    Pure and deterministic: the same sites and the same settings always
    give the same tree, which is what lets :func:`sync` decide whether
    anything needs writing by comparing it to what caddy holds.

    One http server (``cervo``) on cervo's port, cervo's own reverse proxy
    first and one ``file_server`` route per site after it. Under https a
    ``tls`` app comes along: an automation policy for cervo's own hostname
    with the operator's ``ACME_EMAIL``, and one per site with its owner's.
    """
    tree: dict[str, Any] = {
        "http": {
            "servers": {
                "cervo": {
                    "listen": [":443" if config.SCHEME == "https" else ":80"],
                    "routes": [_apex_route(), *(_route(site) for site in sites)],
                }
            }
        }
    }
    if config.SCHEME == "https":
        tree["tls"] = {
            "automation": {
                "policies": [_apex_policy(), *(_policy(site) for site in sites)]
            }
        }
    return tree


def sync(sites: list[Route]) -> bool:
    """Make caddy's running config the one the database describes.

    Returns whether anything was written. The normal case — a periodic
    reconciliation of a stack already in step — is one read and no write at
    all, which matters because every write makes caddy reload.

    Otherwise the whole tree goes in one call, so caddy is never caught
    half-updated and nothing an older cervo (or anyone else) left in
    ``apps`` survives: a PUT when caddy holds no ``apps`` yet, since a PUT
    creates the path it is given, and a PATCH when it does, since a PUT onto
    an existing key is refused as a conflict. The two are one intent, so a
    config that changed underfoot between the read and the write — someone
    loading one by hand, a caddy that finished resuming a moment late — is
    answered by the other method rather than by a failed job.
    """
    desired = apps(sites)
    current = _current_apps()
    if current == desired:
        return False
    method = "PUT" if current is None else "PATCH"
    status, response = _api(method, _APPS, desired)
    if status == 409:  # "key already exists": apps appeared after the read
        method = "PATCH"
        status, response = _api(method, _APPS, desired)
    if status not in (200, 201):
        raise _refused(f"{method} {_APPS}", status, response)
    return True


def _current_apps() -> dict | None:
    """What caddy is running, or None when it holds nothing there.

    Two shapes read as nothing: a caddy whose config has no ``apps`` key
    answers the GET with null, and one with no config at all cannot even
    traverse to it and refuses with a 400 — the shape a caddy that has just
    booted with no autosave to resume is in.
    """
    status, body = _api("GET", _APPS)
    if status == 200:
        return body
    if status == 400:  # "invalid traversal path": the config itself is null
        return None
    raise _refused("read its running config", status, body)


def _apex_route() -> dict[str, Any]:
    """cervo's own hostname, reverse-proxied to the MCP server and website."""
    return {
        "@id": _APEX_ID,
        "match": [{"host": [config.DOMAIN]}],
        "handle": [
            {"handler": "reverse_proxy", "upstreams": [{"dial": config.MCP_UPSTREAM}]}
        ],
        "terminal": True,
    }


def _route(site: Route) -> dict[str, Any]:
    """The route serving one site's directory straight off the volume."""
    return {
        "@id": f"{_ROUTE_ID}{site.slug}",
        "match": [{"host": [f"{site.slug}.{config.DOMAIN}"]}],
        "handle": [
            {"handler": "file_server", "root": str(config.DATA_DIR / site.slug)}
        ],
        "terminal": True,
    }


def _apex_policy() -> dict[str, Any]:
    """The TLS automation policy for cervo's own hostname.

    With an ``ACME_EMAIL`` this is exactly what the Caddyfile adapter
    produced for the apex: both issuers carrying the operator's address.
    Without one the adapter emitted no policy at all, so the single
    unaddressed Let's Encrypt issuer here is cervo's own choice — no
    contact means no ZeroSSL account to fall back to. Production always
    sets ``ACME_EMAIL`` (the environment file does), and dev and test are
    http, where no ``tls`` app is built at all.
    """
    issuers: list[dict[str, Any]] = [{"module": "acme"}]
    if config.ACME_EMAIL:
        issuers = _issuers(config.ACME_EMAIL)
    return {"@id": _APEX_POLICY_ID, "subjects": [config.DOMAIN], "issuers": issuers}


def _policy(site: Route) -> dict[str, Any]:
    """The TLS automation policy issuing one site's certificate.

    The operator's ``ACME_EMAIL`` covers cervo's own hostname only; each
    hosted site registers its owner instead, so that owner is who the CA
    writes to about their certificate.
    """
    return {
        "@id": f"{_POLICY_ID}{site.slug}",
        "subjects": [f"{site.slug}.{config.DOMAIN}"],
        "issuers": _issuers(site.owner_email),
    }


def _issuers(email: str) -> list[dict[str, Any]]:
    """Let's Encrypt, then ZeroSSL, both writing to this address."""
    return [
        {"module": "acme", "email": email},
        {"module": "acme", "ca": _FALLBACK_CA, "email": email},
    ]


def _refused(what: str, status: int, body: Any) -> CaddyError:
    """Caddy's refusal, as an error a human can act on."""
    detail = body.get("error") if isinstance(body, dict) else body
    return CaddyError(f"caddy could not {what} ({status}): {detail}")


def _api(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    """Call caddy's admin API. The one seam: tests replace exactly this.

    Returns the status and the parsed body (None when there is none), and
    never raises on a status — a caddy with no config answers the read with
    a 400 that callers read as "nothing there yet", not as a failure. A
    caddy that cannot be reached at all does raise, and the job retries.
    """
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"{config.CADDY_ADMIN_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.status, _parse(response.read())
    except urllib.error.HTTPError as error:  # a status, not a failure
        return error.code, _parse(error.read())


def _parse(raw: bytes) -> Any:
    """Caddy's body: JSON when it is JSON, the plain text otherwise."""
    text = raw.decode(errors="replace").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
