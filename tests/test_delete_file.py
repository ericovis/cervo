"""The delete_file tool: submission checks, the single-job chain, progress.

Bad paths, other people's sites, and files that do not exist fail the tool
call at once; the removal itself runs as a worker job carrying the owner's
id, so a slug freed and re-taken mid-flight never costs the new owner a
file — that job fails for good, no retries.
"""

import asyncio

import pytest
from fastmcp.exceptions import ToolError

from cervo import server, user, website, worker
from cervo.db import connect
from tests.conftest import OWNER, call, chat, deploy

HTML = "<!doctype html><title>hi</title><h1>Hello</h1>"


def created(slug: str, email: str = OWNER) -> website.Website:
    with connect() as conn:
        owner = user.ensure(conn, email)
        return website.create(conn, slug, owner)


def owner_id(email: str = OWNER) -> int:
    with connect() as conn:
        return user.ensure(conn, email).id


def state_of(slug: str, path: str, user_id: int) -> website.FileDeletion:
    with connect() as conn:
        state = website.file_deletion_state(conn, slug, path, user_id)
    assert state is not None
    return state


async def write(slug: str, path: str) -> None:
    async with chat() as c:
        await call(c, "write_file", slug=slug, path=path, content=HTML)
    deploy()


@pytest.mark.parametrize(
    "path", ["../evil.html", "/etc/passwd.css", "a//b.html", "notes.txt", "x.HTML"]
)
async def test_an_unsafe_or_foreign_path_is_rejected(path):
    created("mysite")
    deploy()
    async with chat() as c:
        with pytest.raises(ToolError):
            await call(c, "delete_file", slug="mysite", path=path)


async def test_deleting_from_someone_elses_site_is_refused():
    created("mysite")
    deploy()
    async with chat("intruder@example.com") as c:
        with pytest.raises(ToolError, match="someone else"):
            await call(c, "delete_file", slug="mysite", path="index.html")


async def test_deleting_from_a_site_that_does_not_exist_is_refused():
    async with chat() as c:
        with pytest.raises(ToolError, match="no site"):
            await call(c, "delete_file", slug="nowhere", path="index.html")


async def test_deleting_a_file_that_does_not_exist_is_refused():
    created("mysite")
    deploy()
    async with chat() as c:
        with pytest.raises(ToolError, match="no file"):
            await call(c, "delete_file", slug="mysite", path="ghost.html")


async def test_a_written_file_is_deleted_through_the_chain(data_dir, caddy_reloads):
    created("mysite")
    deploy()
    await write("mysite", "blog/post.html")
    assert (data_dir / "mysite" / "blog" / "post.html").exists()
    reloads_before = list(caddy_reloads)

    async with chat() as c:
        result = await c.call_tool(
            "delete_file", {"slug": "mysite", "path": "blog/post.html"}
        )
    submitted = result.structured_content
    assert submitted["status"] == "pending"  # conftest pins the window to zero
    assert submitted["steps_total"] == 1

    assert deploy() == 1  # the one delete step
    assert not (data_dir / "mysite" / "blog" / "post.html").exists()
    assert not (data_dir / "mysite" / "blog").exists()  # emptied folder pruned
    assert (data_dir / "mysite" / "index.html").exists()  # the site itself stays
    state = state_of("mysite", "blog/post.html", owner_id())
    assert (state.status, state.error) == ("done", None)
    assert caddy_reloads == reloads_before  # no reload: the file server notices


async def test_deleting_a_custom_index_restores_the_default_page(data_dir):
    created("mysite")
    deploy()
    async with chat() as c:
        await call(c, "write_file", slug="mysite", path="index.html", content=HTML)
    deploy()
    assert (data_dir / "mysite" / "index.html").read_text() == HTML

    async with chat() as c:
        await call(c, "delete_file", slug="mysite", path="index.html")
    deploy()
    page = (data_dir / "mysite" / "index.html").read_text()
    assert page != HTML  # the custom page is gone...
    assert "mysite" in page  # ...and the default landing page is back


async def test_a_slug_retaken_mid_flight_loses_no_file(data_dir):
    created("mysite")
    deploy()
    await write("mysite", "page.html")
    async with chat() as c:
        await call(c, "delete_file", slug="mysite", path="page.html")  # queued

    with connect() as conn:
        owner = user.ensure(conn, OWNER)
        website.delete(conn, "mysite", owner)
        newcomer = user.ensure(conn, "newcomer@example.com")
        website.create(conn, "mysite", newcomer)

    assert worker.run_once()  # the stale deletion runs first, and must refuse
    assert (data_dir / "mysite" / "page.html").exists()  # nothing was touched
    state = state_of("mysite", "page.html", owner_id())
    assert state.status == "failed"
    assert "deleted" in state.error
    deploy()  # the site delete and the newcomer's deployment; no retries
    assert deploy() == 0


async def test_an_identical_deletion_in_flight_is_not_queued_twice():
    created("mysite")
    deploy()
    async with chat() as c:
        await call(c, "delete_file", slug="mysite", path="index.html")
        again = await c.call_tool(
            "delete_file", {"slug": "mysite", "path": "index.html"}
        )
    assert again.structured_content["status"] == "pending"
    with connect() as conn:
        (count,) = conn.execute(
            "SELECT count(*) FROM job WHERE kind = ?", (website.DELETE_FILE_KIND,)
        ).fetchone()
    assert count == 1
    assert deploy() == 1


async def test_a_followed_deletion_streams_progress(monkeypatch, data_dir):
    """A client that sends a progress token sees the step and gets 'done'."""
    created("followed")
    deploy()
    await write("followed", "blog/post.html")
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
                "delete_file",
                {"slug": "followed", "path": "blog/post.html"},
                progress_handler=on_progress,
            )
        finally:
            pumping.cancel()

    state = result.structured_content
    assert state["status"] == "done"
    steps = [progress for progress, _, _ in updates]
    assert steps[0] == 0 and steps[-1] == 1
    assert steps == sorted(steps)
    assert all(total == 1 for _, total, _ in updates)
    assert updates[-1][2] == "deleted blog/post.html from the site"
    assert not (data_dir / "followed" / "blog" / "post.html").exists()
