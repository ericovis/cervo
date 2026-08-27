"""The deployment-progress app: create_website's UI and the tool it polls."""

import json

import pytest
from fastmcp.exceptions import ToolError

from tests.conftest import call, chat, deploy, sign_in

DEPLOYMENT_URI = "ui://cervo/deployment.html"


async def test_create_website_declares_the_progress_ui():
    async with chat() as c:
        tools = {tool.name: tool for tool in await c.list_tools()}

    assert tools["create_website"].meta["ui"] == {"resourceUri": DEPLOYMENT_URI}


async def test_website_status_is_only_for_the_app():
    async with chat() as c:
        tools = {tool.name: tool for tool in await c.list_tools()}

    assert tools["website_status"].meta["ui"] == {"visibility": ["app"]}


async def test_the_app_can_follow_a_deployment(mailbox):
    async with chat() as c:
        await sign_in(c, mailbox)
        await call(c, "create_website", slug="watched")

    # The app polls from the same conversation, but the tool itself does not
    # care: a fresh unauthenticated chat models the weakest caller.
    async with chat() as page:
        before = json.loads(await call(page, "website_status", slug="watched"))
        deploy()
        after = json.loads(await call(page, "website_status", slug="watched"))

    assert before["status"] == "pending"
    assert after["status"] == "live"
    assert after["url"] == "http://watched.localhost"


async def test_the_status_of_nothing_is_an_error():
    async with chat() as c:
        with pytest.raises(ToolError, match="no site"):
            await call(c, "website_status", slug="ghost")


async def test_the_progress_ui_is_served_as_an_mcp_app():
    async with chat() as c:
        contents = await c.read_resource(DEPLOYMENT_URI)

    page = contents[0].text
    assert contents[0].mimeType == "text/html;profile=mcp-app"
    assert "--accent" in page  # the design system's token block
    assert "website_status" in page  # what the page polls
