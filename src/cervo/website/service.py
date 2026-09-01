"""Creating, listing, and deleting sites, on behalf of the user who owns them.

Creating a site writes the row and queues the first job of the deployment
chain; the worker process does the provisioning, one step per job. A site's
``status``, ``error``, and step fields therefore come from its latest
deployment job, attached here whenever a site is read.
"""

import sqlite3
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from cervo import config, job
from cervo.errors import AppError
from cervo.user.types import User
from cervo.website import _dao
from cervo.website.types import FileDeletion, FileWrite, Route, Website, WebsiteStatus

DELETE_KIND = "website.delete"

# A deployment is a chain of jobs: each one's success has the worker enqueue
# the next, so a site's progress is visible one step at a time.
PROVISION_KIND = "website.provision"
CONFIGURE_KIND = "website.configure"
ACTIVATE_KIND = "website.activate"
DEPLOY_CHAIN = (PROVISION_KIND, CONFIGURE_KIND, ACTIVATE_KIND)

# What each step of the chain is doing, in words fit for a progress report.
_STEP_LABELS = {
    PROVISION_KIND: "writing the site's files",
    CONFIGURE_KIND: "updating the web server config",
    ACTIVATE_KIND: "routing traffic to the site",
}

# The Caddyfile and caddy itself are shared by every site, so the jobs that
# rewrite or reload them run one at a time — as a group, so a delete can never
# render its stale snapshot over a configure that just added another site,
# however many workers there are.
_CADDYFILE_GROUP = "caddyfile"
job.serialize(CONFIGURE_KIND, _CADDYFILE_GROUP)
job.serialize(ACTIVATE_KIND, _CADDYFILE_GROUP)
job.serialize(DELETE_KIND, _CADDYFILE_GROUP)

# Writing a file into a site is its own chain, so it can grow more steps
# (a virus scan, say) without touching the queue machinery.
VALIDATE_FILE_KIND = "website.validate_file"
WRITE_FILE_KIND = "website.write_file"
FILE_CHAIN = (VALIDATE_FILE_KIND, WRITE_FILE_KIND)

_FILE_STEP_LABELS = {
    VALIDATE_FILE_KIND: "checking the file's content",
    WRITE_FILE_KIND: "writing the file",
}

# Deleting a file is a single-job chain on purpose: a future step (say,
# purging a cache) is one more entry here rather than new machinery.
DELETE_FILE_KIND = "website.delete_file"
DELETE_FILE_CHAIN = (DELETE_FILE_KIND,)

_DELETE_FILE_STEP_LABELS = {DELETE_FILE_KIND: "deleting the file"}

MAX_FILE_BYTES = 1024 * 1024
_ALLOWED_SUFFIXES = frozenset({".html", ".css"})

# Per-owner and per-site limits, so one tester cannot exhaust the shared VPS.
# Generous enough that ordinary use never meets them.
_MAX_SITES_PER_USER = 25
_MAX_FILES_PER_SITE = 100

# DATA_DIR/caddyfile would collide with the rendered DATA_DIR/Caddyfile on a
# case-insensitive filesystem (macOS development).
_RESERVED = frozenset({"caddyfile"})

# How a chain job's generic status reads as a site's status.
_SITE_STATUS: dict[str, WebsiteStatus] = {
    "pending": "pending",
    "working": "deploying",
    "done": "live",
    "failed": "failed",
}


class WebsiteError(AppError):
    """Raised when a website cannot be created."""


def create_tables(conn: sqlite3.Connection) -> None:
    """Create this domain's storage. Safe to call on every startup."""
    _dao.create_tables(conn)


def create(conn: sqlite3.Connection, slug: str, owner: User) -> Website:
    """Create a site owned by ``owner`` and queue its deployment.

    Raises if the slug is reserved or belongs to someone else. Called on a
    site ``owner`` already has: a failed deployment is queued again, anything
    else is refused — it is either live or already on its way.
    """
    if slug in _RESERVED:
        raise WebsiteError(f"The slug {slug!r} is reserved.")

    # The insert is atomic and decides ownership: a second creator racing for
    # the same fresh slug conflicts and gets None here, never the row.
    created = _dao.insert_if_absent(conn, slug, owner.id)
    if created is not None:
        # The quota applies to new sites only: re-deploying one you already own
        # falls to the branch below. Count includes the row just inserted, so
        # roll it back and refuse if it put the owner over the limit.
        if _dao.count_for_user(conn, owner.id) > _MAX_SITES_PER_USER:
            _dao.delete(conn, slug)
            raise WebsiteError(
                f"You can host at most {_MAX_SITES_PER_USER} sites. "
                "Delete one before creating another."
            )
        job.enqueue(conn, DEPLOY_CHAIN[0], {"slug": slug})
        return _with_deployment(conn, created)

    existing = _dao.get(conn, slug)
    if existing is None or existing.user_id != owner.id:
        raise WebsiteError(f"The slug {slug!r} is already taken.")

    deployment = job.latest_of(conn, DEPLOY_CHAIN, {"slug": slug})
    if deployment is None or deployment.status == "failed":
        job.enqueue(conn, DEPLOY_CHAIN[0], {"slug": slug})
        return _with_deployment(conn, existing)
    site = existing.model_copy(update=_deployment_state(deployment))
    if site.status == "live":
        raise WebsiteError(f"You already own {slug!r}, and it is live.")
    raise WebsiteError(f"You already own {slug!r}; its deployment is in progress.")


def _owned(conn: sqlite3.Connection, slug: str, owner: User) -> Website:
    """The owner's site with this slug; raises if absent or someone else's."""
    site = _dao.get(conn, slug)
    if site is None:
        raise WebsiteError(f"There is no site with the slug {slug!r}.")
    if site.user_id != owner.id:
        raise WebsiteError(f"The site {slug!r} belongs to someone else.")
    return site


def delete(conn: sqlite3.Connection, slug: str, owner: User) -> None:
    """Delete ``owner``'s site and queue the removal of its traces.

    The row goes immediately — the slug is free again and the site stops
    being listed — and a worker job then takes the route out of the
    Caddyfile and deletes the site's directory. Raises if there is no such
    site or it belongs to someone else.
    """
    _owned(conn, slug, owner)
    _dao.delete(conn, slug)
    job.enqueue(conn, DELETE_KIND, {"slug": slug})


def get(conn: sqlite3.Connection, slug: str) -> Website | None:
    """The site using this slug, if any, with its deployment state."""
    site = _dao.get(conn, slug)
    return _with_deployment(conn, site) if site else None


def for_user(conn: sqlite3.Connection, owner: User) -> list[Website]:
    """Every site ``owner`` has created, with its deployment state."""
    return _attach_deployments(conn, _dao.for_user(conn, owner.id))


def routes(conn: sqlite3.Connection) -> list[Route]:
    """Every site with its owner's email, for the web server's config."""
    return _dao.routes(conn)


def live(conn: sqlite3.Connection) -> list[Website]:
    """Every site whose latest deployment is live, for the public catalog."""
    sites = _attach_deployments(conn, _dao.all_sites(conn))
    return [site for site in sites if site.status == "live"]


def _attach_deployments(
    conn: sqlite3.Connection, sites: list[Website]
) -> list[Website]:
    """Fill in each site's deployment state, in one query, not one per site."""
    deployments = job.latest_of_grouped(
        conn, DEPLOY_CHAIN, "slug", [site.slug for site in sites]
    )
    return [
        site.model_copy(update=_deployment_state(deployments[site.slug]))
        if site.slug in deployments
        else site
        for site in sites
    ]


def submit_file(
    conn: sqlite3.Connection, slug: str, path: str, content: str, owner: User
) -> FileWrite:
    """Queue writing ``content`` into ``owner``'s site at ``path``.

    Everything that needs no job is settled right here and raises on
    failure: the site must be the owner's, the path a safe relative
    ``.html``/``.css`` path, the content text within the size cap. What
    remains — content validation, the write itself — runs as a chain of
    worker jobs, whose payload carries the owner's id so a slug freed and
    re-taken meanwhile never lets a stale write land in someone else's
    site; an identical submission already in flight is returned instead of
    being queued twice.
    """
    _owned(conn, slug, owner)
    file_target(slug, path)
    try:
        size = len(content.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise WebsiteError("The content is not valid text.") from error
    if size > MAX_FILE_BYTES:
        raise WebsiteError("Files are limited to 1 MiB.")
    _within_file_quota(slug, path)

    payload = {"slug": slug, "path": path, "content": content, "user_id": owner.id}
    current = job.latest_of(conn, FILE_CHAIN, payload)
    if current is not None and current.status in ("pending", "running"):
        return _file_write(slug, path, current)
    job.enqueue(conn, FILE_CHAIN[0], payload)
    return FileWrite(
        slug=slug,
        path=path,
        step=_FILE_STEP_LABELS[FILE_CHAIN[0]],
        steps_total=len(FILE_CHAIN),
    )


def file_state(
    conn: sqlite3.Connection, slug: str, path: str, content: str, user_id: int
) -> FileWrite | None:
    """The state of the newest chain writing exactly this file, if any."""
    payload = {"slug": slug, "path": path, "content": content, "user_id": user_id}
    link = job.latest_of(conn, FILE_CHAIN, payload)
    return _file_write(slug, path, link) if link else None


def submit_file_deletion(
    conn: sqlite3.Connection, slug: str, path: str, owner: User
) -> FileDeletion:
    """Queue deleting the file at ``path`` from ``owner``'s site.

    The site must be the owner's, the path a safe relative
    ``.html``/``.css`` path, and the file must actually exist; each failure
    raises right here. The removal itself runs as a worker job — carrying
    the owner's id, so a slug freed and re-taken meanwhile never costs
    someone else a file — and an identical deletion already in flight is
    returned instead of being queued twice.
    """
    _owned(conn, slug, owner)
    if not file_target(slug, path).is_file():
        raise WebsiteError(f"There is no file at {path!r} in {slug!r}.")

    payload = {"slug": slug, "path": path, "user_id": owner.id}
    current = job.latest_of(conn, DELETE_FILE_CHAIN, payload)
    if current is not None and current.status in ("pending", "running"):
        return _file_deletion(slug, path, current)
    job.enqueue(conn, DELETE_FILE_CHAIN[0], payload)
    return FileDeletion(
        slug=slug,
        path=path,
        step=_DELETE_FILE_STEP_LABELS[DELETE_FILE_CHAIN[0]],
        steps_total=len(DELETE_FILE_CHAIN),
    )


def file_deletion_state(
    conn: sqlite3.Connection, slug: str, path: str, user_id: int
) -> FileDeletion | None:
    """The state of the newest chain deleting exactly this file, if any."""
    payload = {"slug": slug, "path": path, "user_id": user_id}
    link = job.latest_of(conn, DELETE_FILE_CHAIN, payload)
    return _file_deletion(slug, path, link) if link else None


def _within_file_quota(slug: str, path: str) -> None:
    """Refuse a new file once a site is at its file limit (best-effort).

    Overwrites do not count. The count is a disk read, so a burst of brand-new
    paths can momentarily overshoot before any land — a soft cap for a shared
    demo, not an accountant.
    """
    if file_target(slug, path).exists():
        return
    site_dir = config.DATA_DIR / slug
    if not site_dir.is_dir():
        return
    files = sum(1 for entry in site_dir.rglob("*") if entry.is_file())
    if files >= _MAX_FILES_PER_SITE:
        raise WebsiteError(
            f"A site can hold at most {_MAX_FILES_PER_SITE} files. "
            "Delete one before adding another."
        )


def file_job_superseded(conn: sqlite3.Connection, claimed: job.Job) -> bool:
    """Whether a newer write/delete job for the same file exists.

    A file's writes and deletions all target one slug+path, so an older job
    whose retry runs after a newer submission would revert it — or resurrect
    a file a later deletion removed. The worker skips a write/delete job when
    any FILE_CHAIN or DELETE_FILE_CHAIN job with a higher id (a later
    submission) exists for the same slug+path.
    """
    payload = claimed.payload
    newest = job.newest_id(
        conn,
        (*FILE_CHAIN, *DELETE_FILE_CHAIN),
        {"slug": payload["slug"], "path": payload["path"]},
    )
    return newest is not None and newest > claimed.id


def file_target(slug: str, path: str) -> Path:
    """The absolute location ``path`` names inside the site's directory.

    The one place a user-supplied path is joined to the filesystem: the
    path is checked, joined, resolved, and required to land inside the
    site's own directory — a symlink pointing out of it is caught too.
    """
    _check_path(path)
    root = (config.DATA_DIR / slug).resolve()
    target = (config.DATA_DIR / slug / path).resolve()
    if not target.is_relative_to(root):
        raise WebsiteError(f"The path {path!r} escapes the site's directory.")
    return target


def check_content(path: str, content: str) -> None:
    """Refuse content that is not plausibly what its extension claims.

    A sanity check, not a linter: the content must be real text (UTF-8,
    no NULs) and survive a structural read — HTML through the stdlib
    tokenizer, CSS through a small scanner. Nothing is executed, and
    malformed-but-plausible markup passes, just as a browser would take it.
    """
    if "\x00" in content:
        raise WebsiteError("The content contains NUL bytes, so it is not text.")
    try:
        content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise WebsiteError("The content is not valid text.") from error
    if PurePosixPath(path).suffix == ".css":
        _check_css(content)
    else:
        _check_html(content)


def exists(conn: sqlite3.Connection, slug: str) -> bool:
    """Whether a site with this slug has been created."""
    return _dao.exists(conn, slug)


def _check_path(path: str) -> None:
    """Refuse anything but a safe relative path to an .html or .css file.

    Redundant with the ``FilePath`` pattern at the tool boundary on
    purpose — the service must be safe on its own.
    """
    if "\\" in path or "\x00" in path or path.startswith("/"):
        raise WebsiteError(f"The path {path!r} is not a safe relative path.")
    if any(part == "" or part.startswith(".") for part in path.split("/")):
        raise WebsiteError(f"The path {path!r} is not a safe relative path.")
    if PurePosixPath(path).suffix not in _ALLOWED_SUFFIXES:
        raise WebsiteError("Only .html and .css files can be written.")


def _check_html(content: str) -> None:
    """Feed the content through the stdlib HTML tokenizer.

    The tokenizer is as lenient as browsers are — unbalanced or sloppy
    markup passes — so failing it means the content is not HTML at all.
    """
    parser = HTMLParser(convert_charrefs=True)
    try:
        parser.feed(content)
        parser.close()
    except Exception as error:  # any tokenizer failure is the verdict
        raise WebsiteError(f"The content is not valid HTML: {error}") from error


def _check_css(content: str) -> None:
    """A structural read of CSS: no stray closing braces, no HTML.

    Comments and strings are skipped so braces inside them do not count.
    An unclosed brace or comment is tolerated — plausible hand-written
    CSS is the bar, not a clean parse.
    """
    depth = 0
    state = None  # None, "comment", or the quote character of a string
    escaped = False
    meaningful = False  # any non-whitespace seen outside a comment
    index = 0
    while index < len(content):
        char = content[index]
        if state == "comment":
            if char == "*" and content[index + 1 : index + 2] == "/":
                state = None
                index += 1
        elif state is not None:  # inside a string
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == state:
                state = None
        elif char == "/" and content[index + 1 : index + 2] == "*":
            state = "comment"
            index += 1
        elif char == "<" and not meaningful:
            raise WebsiteError("The content reads as HTML, not CSS.")
        else:
            if char in "'\"":
                state = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth < 0:
                    raise WebsiteError("The content is not valid CSS: stray '}'.")
            if not char.isspace():
                meaningful = True
        index += 1


def _file_deletion(slug: str, path: str, link: job.Job) -> FileDeletion:
    """The file deletion ``link`` is the newest job of, with its chain state."""
    state = _chain_state(DELETE_FILE_CHAIN, _DELETE_FILE_STEP_LABELS, link)
    return FileDeletion(slug=slug, path=path, **state)


def _file_write(slug: str, path: str, link: job.Job) -> FileWrite:
    """The file write ``link`` is the newest job of, with its chain state."""
    state = _chain_state(FILE_CHAIN, _FILE_STEP_LABELS, link)
    return FileWrite(slug=slug, path=path, **state)


def _with_deployment(conn: sqlite3.Connection, site: Website) -> Website:
    """The site carrying the state of its latest deployment job."""
    deployment = job.latest_of(conn, DEPLOY_CHAIN, {"slug": site.slug})
    if deployment is None:
        return site
    return site.model_copy(update=_deployment_state(deployment))


def _deployment_state(deployment: job.Job) -> dict:
    """How a deployment job reads as a site's status, error, and step.

    The job also says how far along the chain the site is.
    """
    state = _chain_state(DEPLOY_CHAIN, _STEP_LABELS, deployment)
    return {**state, "status": _SITE_STATUS[state["status"]]}


def _chain_state(chain: tuple[str, ...], labels: dict[str, str], link: job.Job) -> dict:
    """How the newest job of a chain reads as the whole chain's state.

    Returned with the generic statuses pending/working/done/failed;
    callers map them to their own vocabulary. A chain job marked done is
    a moment the worker's transaction makes invisible — the next step is
    enqueued as the previous one succeeds — but it is still mapped, not
    trusted to never surface.
    """
    total = len(chain)
    state: dict = {"error": link.error, "steps_total": total, "step": None}
    index = chain.index(link.kind)
    if link.status == "done":
        if index == total - 1:
            return {**state, "status": "done", "steps_done": total}
        state |= {"step": labels[chain[index + 1]], "steps_done": index + 1}
        return {**state, "status": "working"}

    state |= {"step": labels[link.kind], "steps_done": index}
    if link.status == "failed":
        return {**state, "status": "failed"}
    if link.status == "pending" and index == 0:
        return {**state, "status": "pending"}
    return {**state, "status": "working"}
