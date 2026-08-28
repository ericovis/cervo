"""What reaches Honeybadger: defects with their context — and nothing else."""

import pytest
from fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from cervo import config, job, monitoring, website, worker
from cervo.db import connect
from cervo.server import app
from tests.conftest import OWNER, chat, deploy


async def test_a_defect_inside_a_tool_is_reported_with_its_request(
    reports, monkeypatch
):
    def defect(conn, owner):
        raise RuntimeError("the disk fell off")

    monkeypatch.setattr(website, "for_user", defect)

    async with chat() as c:
        with pytest.raises(ToolError):
            await c.call_tool("list_websites")

    (sent,) = reports
    assert isinstance(sent["exception"], RuntimeError)
    context = sent["context"]
    assert context["component"] == "mcp"
    assert context["method"] == "tools/call"
    assert context["name"] == "list_websites"
    assert context["user_id"]
    assert context["user_email"] == OWNER


async def test_errors_that_are_answers_are_not_reported(reports):
    async with chat() as c:
        with pytest.raises(ToolError):  # a reserved slug — a refusal to read
            await c.call_tool("create_website", {"slug": "caddyfile"})
        with pytest.raises(ToolError):  # unknown tool — the client's problem
            await c.call_tool("no_such_tool")
    assert reports == []


def test_a_failed_job_is_reported_with_its_job(reports):
    with connect() as conn:
        queued = job.enqueue(conn, "website.explode", {"slug": "doomed"})

    assert worker.run_once()

    (sent,) = reports
    assert isinstance(sent["exception"], RuntimeError)
    context = sent["context"]
    assert context["component"] == "worker"
    assert context["kind"] == "website.explode"
    assert context["job_id"] == queued.id
    assert context["attempt"] == 1
    assert context["slug"] == "doomed"
    assert context["permanent"] is False


def test_a_permanent_failure_reports_the_content_size_it_sheds(reports):
    payload = {
        "slug": "ghost",  # no such site: the validation is a verdict
        "path": "index.html",
        "content": "<p>hi</p>",
        "user_id": 1,
    }
    with connect() as conn:
        job.enqueue(conn, website.VALIDATE_FILE_KIND, payload)

    assert worker.run_once()

    (sent,) = reports
    context = sent["context"]
    assert context["permanent"] is True
    assert "content" not in context
    assert context["content_bytes"] == len("<p>hi</p>")
    assert context["user_id"] == 1


async def test_every_processed_job_emits_an_insights_event(insights):
    async with chat() as c:
        await c.call_tool("create_website", {"slug": "metrics"})
    assert deploy() == 3

    processed = [data for kind, data in insights if kind == "job.processed"]
    assert [p["outcome"] for p in processed] == ["done", "done", "done"]
    assert {p["slug"] for p in processed} == {"metrics"}
    assert all(p["duration_ms"] >= 0 for p in processed)


def test_the_wrapped_app_still_serves_pages():
    wrapped = monitoring.wrap(app.http_app(stateless_http=True))
    with TestClient(wrapped) as client:
        assert client.get("/").status_code == 200


def test_without_a_key_the_app_is_left_untouched(monkeypatch):
    monkeypatch.setattr(config, "HONEYBADGER_API_KEY", "")
    application = app.http_app(stateless_http=True)
    assert monitoring.wrap(application) is application
