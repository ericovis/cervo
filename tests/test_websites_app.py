"""The websites-overview app: list_websites' UI."""

import json

from tests.conftest import call, chat, deploy, sign_in

WEBSITES_URI = "ui://cervo/websites.html"


async def test_list_websites_declares_the_overview_ui():
    async with chat() as c:
        tools = {tool.name: tool for tool in await c.list_tools()}

    assert tools["list_websites"].meta["ui"] == {"resourceUri": WEBSITES_URI}


async def test_the_result_is_one_text_block_the_page_can_parse(mailbox):
    """The UI reads the first text content block as the whole JSON list."""
    async with chat() as c:
        await sign_in(c, mailbox)
        await call(c, "create_website", slug="one")
        deploy()
        await call(c, "create_website", slug="two")
        result = await c.call_tool("list_websites")

    texts = [block for block in result.content if block.type == "text"]
    assert len(texts) == 1
    sites = {site["slug"]: site for site in json.loads(texts[0].text)}
    assert sites["one"]["status"] == "live"
    assert sites["two"]["status"] == "pending"


async def test_the_overview_ui_is_served_as_an_mcp_app():
    async with chat() as c:
        contents = await c.read_resource(WEBSITES_URI)

    page = contents[0].text
    assert contents[0].mimeType == "text/html;profile=mcp-app"
    assert "--accent" in page  # the design system's token block
    assert "website_status" in page  # what the page polls for unsettled sites
