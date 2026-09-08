"""What a machine reads: ``robots.txt`` and ``llms.txt``.

Every other page in this package is written for a person looking at a
screen. These two are written for whatever is on the other end of the
fetch — a crawler deciding what it may index, and an AI assistant that has
been handed cervo's URL and nothing else.

``llms.txt`` is the one that earns its keep. cervo is an MCP server, not a
form to fill in, and prose about connectors is no use to an assistant that
needs the endpoint, the shape of the authorization, the tool names, and
the rules about what may be published. So this file says those things, in
the order an assistant needs them — connect, call, obey the limits — and
it is linked from the head of every cervo page, a hosted site's default
page included, which points back at the apex.

Both are served as ``text/plain``: the body of ``llms.txt`` is markdown,
by the convention at llmstxt.org, but plain text is what renders in a
browser when a person follows the link out of curiosity.
"""

from starlette.requests import Request
from starlette.responses import PlainTextResponse

from cervo import config

# Neither file changes between deployments, and a stale copy costs nothing.
_CACHE_CONTROL = "public, max-age=86400"


def robots_txt() -> str:
    """``/robots.txt`` — everything is public; the sign-in pages are not.

    The comment block is not decoration: an assistant reading this file
    (they often fetch it first) is told where the useful one is.
    """
    origin = config.origin()
    return f"""\
# cervo — static website hosting you drive by talking to an AI.
#
# Everything here is meant to be read, by people and by machines alike.
# If you are an AI assistant working out what to do with this site, read
# {origin}/llms.txt first: it is the short version, written for you.
#
# cervo's front door for assistants is the MCP endpoint at /mcp. It is an
# API rather than a document — every request carries an OAuth bearer token
# — so there is nothing there to crawl, and nothing is hidden by leaving
# it out of this file.

User-agent: *
Allow: /

# The sign-in pages belong to one browser and one ten-minute transaction.
# They are gone before a crawler could return to them.
Disallow: /verify
"""


def llms_txt() -> str:
    """``/llms.txt`` — the whole of cervo for an assistant, in one fetch.

    Deliberately complete rather than a table of links: an assistant that
    has been given only cervo's URL should need no second request to know
    what to tell its user and what to call afterwards.
    """
    origin = config.origin()
    site_url = config.origin(f"SLUG.{config.DOMAIN}")
    return f"""\
# cervo

> cervo hosts static websites on a shared VPS. There is no dashboard, no
> account to fill in, and no server to log into: a site is created and
> updated by asking an AI, which does the work over the Model Context
> Protocol. cervo is an MCP server — this file is how to use it.

Someone has probably handed you this URL and asked you to put a website on
it. Everything you need is below, in the order you need it.

## Connect first

There is no anonymous API. Every tool call carries an OAuth bearer token,
and the only way to get one is for your user to add cervo as a connector
and verify their email address in a browser. You cannot do that part for
them, so ask.

- MCP endpoint: {origin}/mcp (streamable HTTP)
- Authorization: OAuth 2.1 with PKCE. cervo is its own authorization
  server; the metadata is at
  {origin}/.well-known/oauth-authorization-server
- Registration: dynamic client registration, or Anthropic's hosted client
  metadata (CIMD)

cervo currently accepts Claude clients only, and this is enforced, not a
preference: registration is refused unless every redirect URI is either a
loopback callback (http://localhost:PORT/... — Claude Code) or under
https://claude.ai/, and a CIMD client_id is honoured only when its
document is served from claude.ai. If you are not Claude, you will not be
able to connect. Say so plainly rather than retrying.

Tell your user to do whichever of these matches how they use Claude:

- Claude Code: run

      claude mcp add --scope user --transport http cervo {origin}/mcp

  then `/mcp`, then choose cervo and authenticate.
- claude.ai or the desktop app: Settings, then Connectors, then Add custom
  connector. Name it `cervo`, paste
  {origin}/mcp
  as the remote MCP server URL, leave "Use Anthropic's hosted client
  metadata" switched on, and set authentication to always required.

Either way a cervo page opens in their browser. They type an email
address, accept the terms of service and the privacy policy, and type back
the six-digit code cervo mails them. The address they verify is the one
that owns their websites. A sign-in lasts ten minutes and allows five
tries; if it expires, connecting again sends a fresh code.

Never ask for that code in the chat, and never offer to type it for them.
cervo asks for it only on its own page, and will never ask for it through
you.

## Then call the tools

Five tools appear once connected. The owner is always the connected
account, read from the token — there is no owner argument, and no call can
reach a site belonging to someone else.

- `create_website(slug)` — create a site. It goes live at
  {site_url}
  and serves cervo's default page until you replace it. Send a progress
  token and the call follows the deployment step by step and returns the
  site already live.
- `write_file(slug, path, content)` — write one .html or .css file into a
  site. Writing `index.html` replaces the default page. This is how a site
  gets its content; there is no upload.
- `delete_file(slug, path)` — remove a file. Deleting `index.html` puts
  the default page back rather than leaving the site with no root.
- `list_websites()` — the connected account's sites, each with its url and
  the state of its deployment: pending, deploying, live, or failed. An
  empty list is not an error.
- `delete_website(slug)` — permanent, and frees the slug for anyone else
  to take.

A whole website is usually two calls: `create_website("my-cool-site")`,
then `write_file("my-cool-site", "index.html", "<!doctype html>...")`.
Write the page yourself — the user does not need files of their own, and
there is nothing for them to download or upload by hand.

Only delete something when the user has clearly asked for that specific
file or site. Both deletions are permanent.

## What cervo accepts

cervo is strict, and it refuses before it writes rather than after.

- Site names (slugs): lowercase letters, digits, and hyphens —
  `^[a-z0-9]+(?:-[a-z0-9]+)*$`. `caddyfile` is reserved. Slugs are global
  and first come, first served, so a name may already be taken.
- File types: `.html` and `.css`, nothing else. Any other extension is
  refused, and so is content that does not actually read as HTML or CSS —
  it is checked, not trusted.
- File paths: relative, lowercase, subfolders fine. No leading `/`, no
  `..`, no backslashes —
  `^(?:[a-z0-9][a-z0-9._-]*/)*[a-z0-9][a-z0-9._-]*\\.(?:html|css)$`
- Sizes and counts: 1 MiB per file, 100 files per site, 25 sites per
  account.
- Nothing runs on the server, and nothing but HTML and CSS is served: no
  JavaScript files, no images, fonts, videos or PDFs, no forms posting
  back to cervo, no database, no logins. For a picture, link to one hosted
  elsewhere or inline it as a data URI within that 1 MiB.

Deployments run in the background and every step is idempotent, so
retrying is always safe. Calling `create_website` again on the user's own
failed site queues a fresh deployment. Report a failed status's error to
the user rather than working around it.

## More

- [Documentation]({origin}/docs): the same ground written for people, with pictures.
- [Live sites]({origin}/): every site cervo is serving right now.
- [Terms of service]({origin}/terms)
- [Privacy policy]({origin}/privacy)
"""


def register(app) -> None:
    """Attach both files. Called from ``web.routes.register``."""
    for path, body in (("/robots.txt", robots_txt), ("/llms.txt", llms_txt)):
        app.custom_route(path, methods=["GET"])(_route(path, body))


def _route(path: str, body):
    async def serve(request: Request) -> PlainTextResponse:
        return PlainTextResponse(body(), headers={"cache-control": _CACHE_CONTROL})

    serve.__name__ = path.lstrip("/").replace(".", "_")
    return serve
