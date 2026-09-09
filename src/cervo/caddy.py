"""Caddy, the front door: cervo's own routes are static, the sites are not.

Caddy boots from the Caddyfile checked into the repo (``caddy/Caddyfile``,
mounted read-only): the global options and the reverse proxy to cervo itself,
and nothing else. Every hosted site is published into the *running* config
over caddy's admin API — one route object per site, tagged with an ``@id`` of
``site:{slug}`` so it can be found, replaced, and removed on its own, plus
(under https) one TLS automation policy tagged ``tls:{slug}`` carrying the
owner's email as the certificate's ACME contact.

Nothing here writes a file, and no operation touches another site's route: a
deployment, a deletion, and the periodic reconciliation all edit exactly the
objects they own. Every call goes through :func:`_api`, the one seam tests
replace — nothing else in this module opens a socket.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from cervo import config
from cervo.website.types import Route

_TIMEOUT = 10  # seconds; the admin API is a container away, on a socket

# The ids cervo puts on the objects it owns. Anything untagged is the static
# Caddyfile's and left exactly where it is — see :func:`_ours` for the one
# exception, an older cervo's untagged leftovers.
_ROUTE_ID = "site:"
_POLICY_ID = "tls:"

_TLS_PATH = "/config/apps/tls"
_POLICIES_PATH = f"{_TLS_PATH}/automation/policies"

# The second CA the Caddyfile adapter gives every certificate, cervo's own
# hostname included. A site's policy carries it too: an owner whose
# certificate Let's Encrypt cannot issue (an outage, a rate limit) is no
# worse off than they were when a rendered Caddyfile said `tls {email}`.
_FALLBACK_CA = "https://acme.zerossl.com/v2/DV90"


class CaddyError(RuntimeError):
    """Caddy refused a request — raised with its own explanation."""


@dataclass(frozen=True)
class SyncResult:
    """What a reconciliation changed, for the worker's log.

    All zeros is the normal case and the point of the counts: the periodic
    sync is meant to be a couple of reads and no writes at all.
    """

    routes_added: int = 0
    routes_removed: int = 0
    routes_changed: int = 0
    policies_added: int = 0
    policies_removed: int = 0
    policies_changed: int = 0

    def __str__(self) -> str:
        return (
            f"routes +{self.routes_added} -{self.routes_removed} "
            f"~{self.routes_changed}, policies +{self.policies_added} "
            f"-{self.policies_removed} ~{self.policies_changed}"
        )


def publish(site: Route) -> None:
    """Make caddy serve this site. Idempotent, and quiet when nothing changed.

    A missing route is appended, a stale one replaced, an identical one left
    alone — rewriting nothing matters, because every write makes caddy reload
    its config, and a retried job must not cost the whole server a reload.

    Under https the certificate policy goes in first: a hostname becomes
    caddy's business the moment its route appears, and automatic HTTPS
    starts ordering the certificate on that very reload. With the site's
    own policy already there, that first order is the right one.
    """
    if config.SCHEME == "https":
        _publish_policy(site)

    desired = _route(site)
    identity = f"/id/{_ROUTE_ID}{site.slug}"
    status, current = _api("GET", identity)
    if status == 404:
        _write("POST", _routes_path(), desired)
    elif status == 200:
        if current != desired:
            # PATCH replaces the whole object, ``@id`` included — which is
            # why the desired object carries it.
            _write("PATCH", identity, desired)
    else:
        raise _refused("read the site's route", status, current)


def unpublish(slug: str) -> None:
    """Stop serving this site: drop its route and its certificate policy.

    Both deletions tolerate a 404, so a retried deletion is safe — and the
    policy is deleted even under plain http, where there never was one: one
    cheap call beats a branch that could leave a policy behind after the
    scheme changed.
    """
    for identity in (f"/id/{_ROUTE_ID}{slug}", f"/id/{_POLICY_ID}{slug}"):
        status, body = _api("DELETE", identity)
        if status not in (200, 404):
            raise _refused("delete the site's config", status, body)


def sync(sites: list[Route]) -> SyncResult:
    """Reconcile caddy's running config with the database.

    The safety net: caddy restarted (and came back with only the static
    Caddyfile), or a write was lost. Everything that is not cervo's — the
    apex proxy from the static file — is left exactly where it is, cervo's
    own objects that are still wanted are left where they are too, and only
    what is missing is appended. Each array is written only when it really
    differs, so a stack already in step costs a couple of reads (two under
    http, three under https) and no reload at all.

    The policies go in before the routes, for the reason :func:`publish`
    writes them in that order.
    """
    policies = _sync_policies(sites) if config.SCHEME == "https" else (0, 0, 0)
    return SyncResult(*_sync_routes(sites), *policies)


def _sync_routes(sites: list[Route]) -> tuple[int, int, int]:
    """Make caddy's routes the ones the database says they are."""
    path = _routes_path()
    current = _read_list(path, "read the routes")
    desired = _arranged(current, [_route(site) for site in sites], _ROUTE_ID)
    if desired != current:
        _write("PATCH", path, desired)
    return _counts(current, desired, _ROUTE_ID)


def _sync_policies(sites: list[Route]) -> tuple[int, int, int]:
    """The same reconciliation, for the sites' certificate policies."""
    current = _policies()
    desired = _arranged(current or [], [_policy(site) for site in sites], _POLICY_ID)
    if desired != (current or []):
        # A PUT brings apps.tls → automation → policies with it; a PATCH
        # needs them to be there already.
        _write("PUT" if current is None else "PATCH", _POLICIES_PATH, desired)
    return _counts(current or [], desired, _POLICY_ID)


def _publish_policy(site: Route) -> None:
    """Register the owner's email as the ACME contact for the site's cert.

    The operator's ``ACME_EMAIL`` covers cervo's own hostname only; each
    hosted site gets its own policy, so its owner hears from the CA about
    its certificate. Same upsert as the route, and the same silence when
    nothing changed.
    """
    desired = _policy(site)
    identity = f"/id/{_POLICY_ID}{site.slug}"
    status, current = _api("GET", identity)
    if status == 200:
        if current != desired:
            _write("PATCH", identity, desired)
        return
    if status != 404:
        raise _refused("read the site's certificate policy", status, current)
    _hold_policies()
    _write("POST", _POLICIES_PATH, desired)


def _policies() -> list | None:
    """Caddy's certificate policies, or None when it holds no array at all.

    Read through the whole ``tls`` app in one call rather than at the
    policies path: caddy answers a GET whose *intermediate* segment is
    missing with a 400 ("invalid traversal path"), and only a missing
    *final* one with null. A caddy with no tls app is not the shape this
    Caddyfile adapts to under https, but it is one config away from it, and
    a reconciliation that raises would never repair itself.
    """
    status, tls = _api("GET", _TLS_PATH)
    if status != 200:
        raise _refused("read the tls app", status, tls)
    policies = ((tls or {}).get("automation") or {}).get("policies")
    return policies if isinstance(policies, list) else None


def _hold_policies() -> None:
    """Create the array the TLS policies live in, if it is missing.

    Caddy creates no intermediate objects when appending — a POST into a
    missing parent is an "invalid traversal path" error — but a PUT creates
    the whole path, so one call covers every shape a caddy without policies
    can be in.
    """
    if _policies() is None:
        _write("PUT", _POLICIES_PATH, [])


def _routes_path() -> str:
    """The config path of the routes of the server cervo is served by.

    The Caddyfile adapter names the server itself (``srv0`` today), so the
    name is never assumed: the server is the one listening on cervo's port.
    """
    port = ":443" if config.SCHEME == "https" else ":80"
    status, servers = _api("GET", "/config/apps/http/servers")
    if status != 200:
        raise _refused("read the http servers", status, servers)
    for name, server in (servers or {}).items():
        if port in server.get("listen", []):
            return f"/config/apps/http/servers/{name}/routes"
    raise CaddyError(f"caddy has no server listening on {port}")


def _route(site: Route) -> dict[str, Any]:
    """The running-config route serving one site's directory."""
    return {
        "@id": f"{_ROUTE_ID}{site.slug}",
        "match": [{"host": [f"{site.slug}.{config.DOMAIN}"]}],
        "handle": [
            {"handler": "file_server", "root": str(config.DATA_DIR / site.slug)}
        ],
        "terminal": True,
    }


def _policy(site: Route) -> dict[str, Any]:
    """The TLS automation policy issuing one site's certificate."""
    return {
        "@id": f"{_POLICY_ID}{site.slug}",
        "subjects": [f"{site.slug}.{config.DOMAIN}"],
        "issuers": [
            {"module": "acme", "email": site.owner_email},
            {"module": "acme", "ca": _FALLBACK_CA, "email": site.owner_email},
        ],
    }


def _arranged(current: list, wanted: list, prefix: str) -> list:
    """The array caddy should hold, arranged to keep the writing down.

    What is not cervo's stays exactly where it is (the apex proxy, and the
    policy the operator's ``ACME_EMAIL`` brought with it); cervo's own
    objects stay where they are when they are already right; only what is
    missing is appended. Whatever is cervo's but no site owns falls out.
    Keeping the arrangement is what lets a settled stack compare equal —
    ``publish`` appends in creation order, a sync lists sites by slug, and
    the two serve identically, so reordering must not cost a reload.
    """
    by_key = {_key(obj): obj for obj in wanted}
    arranged = []
    for obj in current:
        if not _ours(obj, prefix):
            arranged.append(obj)
        elif _key(obj) in by_key:
            arranged.append(by_key[_key(obj)])
    placed = {_key(obj) for obj in arranged}
    return [*arranged, *(obj for obj in wanted if _key(obj) not in placed)]


def _counts(current: list, desired: list, prefix: str) -> tuple[int, int, int]:
    """How many of cervo's objects are added, removed, and changed."""
    before = {_key(obj): obj for obj in current if _ours(obj, prefix)}
    after = {_key(obj): obj for obj in desired if _ours(obj, prefix)}
    changed = sum(
        1 for key in before.keys() & after.keys() if before[key] != after[key]
    )
    return len(after.keys() - before.keys()), len(before.keys() - after.keys()), changed


def _ours(obj: Any, prefix: str) -> bool:
    """Whether this object is cervo's to manage.

    Everything cervo writes carries an ``@id``. Anything untagged that is
    about a subdomain of cervo's own domain is claimed as well: that is
    what a version publishing whole rendered Caddyfiles left in the running
    config, and mistaking it for part of the static front door would give
    every site a second, older route for good.
    """
    identity = _identity(obj)
    if identity:
        return identity.startswith(prefix)
    names = _hostnames(obj)
    return bool(names) and all(name.endswith(f".{config.DOMAIN}") for name in names)


def _key(obj: Any) -> str:
    """What names one of cervo's objects across a reconciliation."""
    return _identity(obj) or f"host:{','.join(_hostnames(obj))}"


def _hostnames(obj: Any) -> list[str]:
    """The names an object is about: a route's matchers, a policy's subjects."""
    if not isinstance(obj, dict):
        return []
    if "subjects" in obj:
        return [str(name) for name in obj["subjects"]]
    return [
        str(name) for match in obj.get("match", []) for name in match.get("host", [])
    ]


def _identity(obj: Any) -> str:
    """An object's ``@id``, or "" for anything without one."""
    return str(obj.get("@id", "")) if isinstance(obj, dict) else ""


def _read_list(path: str, what: str) -> list:
    """The array at this config path; empty when caddy has nothing there.

    Only for a path whose parent exists — caddy answers a missing *final*
    segment with null, but a missing intermediate one with a 400.
    """
    status, body = _api("GET", path)
    if status != 200:
        raise _refused(what, status, body)
    return body or []


def _write(method: str, path: str, body: Any) -> None:
    """Change the running config, or raise with caddy's own explanation.

    Every write makes caddy reload — which is why callers write only when
    something actually differs.
    """
    status, response = _api(method, path, body)
    if status not in (200, 201):
        raise _refused(f"{method} {path}", status, response)


def _refused(what: str, status: int, body: Any) -> CaddyError:
    """Caddy's refusal, as an error a human can act on."""
    detail = body.get("error") if isinstance(body, dict) else body
    return CaddyError(f"caddy could not {what} ({status}): {detail}")


def _api(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    """Call caddy's admin API. The one seam: tests replace exactly this.

    Returns the status and the parsed body (None when there is none), and
    never raises on a status — caddy answers a missing object with a 404
    that callers read as "not there yet", not as a failure. A caddy that
    cannot be reached at all does raise, and the job retries.
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
