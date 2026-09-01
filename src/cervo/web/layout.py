"""Shared page chrome: the "deploy receipt" design system in fasttags.

The theme variables come from one file, ``templates/_tokens.css``; the
rest of the CSS and the theme scripts are carried inline here, so every
page is self-contained and makes zero external requests.
"""

from importlib import resources

from fasthtml.common import (
    H1,
    H2,
    A,
    Body,
    Button,
    Code,
    Dd,
    Div,
    Dl,
    Dt,
    Head,
    Header,
    Html,
    Main,
    Meta,
    P,
    Pre,
    Script,
    Section,
    Span,
    Style,
    Title,
    to_xml,
)
from starlette.responses import HTMLResponse

# The theme variables live in one file so every page shares them; see
# templates/_tokens.css.
_TOKENS_CSS = resources.files("cervo").joinpath("templates", "_tokens.css").read_text()

_PAGE_CSS = """
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; background: var(--bg); color: var(--text);
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    font-size: 13px; line-height: 1.65;
  }
  /* The one width in the design: prose, receipts, and figures all run
     the full column, so nothing sits at a different measure. */
  main { max-width: 640px; margin: 0 auto; padding: 44px 24px 56px; }
  a { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }
  a:hover { color: var(--ink); }
  /* ── header ── */
  header { display: flex; justify-content: space-between; align-items: center; }
  .wordmark { color: var(--ink); font-weight: 700; font-size: 12px; text-decoration: none; }
  .header-right { display: flex; align-items: center; gap: 14px; }
  .tagline { color: var(--muted); font-size: 12px; }
  #theme-toggle {
    background: transparent; border: 1px solid var(--rule); border-radius: 4px;
    padding: 3px 10px; font-family: inherit; font-size: 11px; color: var(--muted); cursor: pointer;
  }
  #theme-toggle:hover { color: var(--accent); border-color: var(--accent); }
  /* ── hero ── */
  .status { margin: 40px 0 0; font-size: 12px; letter-spacing: 0.12em; color: var(--accent); }
  h1 { font-size: 26px; color: var(--ink); font-weight: 600; margin: 10px 0 14px; overflow-wrap: anywhere; }
  .intro { margin: 0; color: var(--muted); }
  /* ── receipt ── */
  .receipt { margin: 24px 0 0; display: flex; flex-direction: column; gap: 10px; }
  .receipt > div { display: flex; align-items: baseline; gap: 10px; }
  .receipt dt { color: var(--muted); overflow-wrap: anywhere; }
  .receipt .leader { flex: 1; border-bottom: 1px dotted var(--dotted); min-width: 40px; }
  .receipt dd { margin: 0; overflow-wrap: anywhere; }
  /* ── sections ── */
  section { margin-top: 42px; border-top: 1px solid var(--rule); padding-top: 24px; scroll-margin-top: 16px; }
  h2 { font-size: 11px; letter-spacing: 0.18em; color: var(--accent); font-weight: 400; margin: 0; }
  section p { margin: 10px 0 0; }
  .endpoint { display: inline-block; margin: 14px 0 0; padding: 10px 16px; background: var(--code-bg); border: 1px solid var(--rule); border-radius: 6px; overflow-wrap: anywhere; }
  .endpoint a { text-decoration: none; }
  .steps { margin: 12px 0 0; padding-left: 22px; display: flex; flex-direction: column; gap: 8px; }
  .prompts { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
  .prompts > div { display: flex; gap: 10px; }
  .prompts .caret { color: var(--accent); }
  .prompts .prompt-text { color: var(--ink); }
  .command { margin: 10px 0 0; padding: 10px 14px; background: var(--code-bg); border: 1px solid var(--rule); border-radius: 6px; overflow-x: auto; font-size: 12px; color: var(--ink); }
  /* ── figures ── */
  figure { margin: 16px 0 0; }
  figure svg { display: block; width: 100%; height: auto; font-family: inherit; }
  figcaption { margin-top: 8px; color: var(--muted); font-size: 12px; }
  .steps figure { margin: 14px 0 4px; }
  .note { color: var(--muted); }
  /* ── footer ── */
  .fine p { font-size: 12px; color: var(--muted); }
  .footer-links { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 12px; font-size: 12px; }
  @media (max-width: 480px) { h1 { font-size: 22px; } }
"""

_THEME_BOOT_JS = """
  // Apply a saved theme choice before first paint (no flash).
  (function () {
    try {
      var t = localStorage.getItem("cervo-theme");
      if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
    } catch (e) {}
  })();
"""

_THEME_TOGGLE_JS = """
  (function () {
    var btn = document.getElementById("theme-toggle");
    var mq = matchMedia("(prefers-color-scheme: light)");
    function effective() {
      return document.documentElement.dataset.theme || (mq.matches ? "light" : "dark");
    }
    function label() {
      btn.textContent = effective() === "dark" ? "\\u2600 light" : "\\u263E dark";
    }
    btn.addEventListener("click", function () {
      var next = effective() === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("cervo-theme", next); } catch (e) {}
      label();
    });
    if (mq.addEventListener) mq.addEventListener("change", label);
    label();
  })();
"""


def document(title: str, *content, base: str = "") -> str:
    """A whole page: chrome around ``content``, rendered to HTML.

    ``base`` prefixes every link into cervo itself — empty on cervo's own
    pages, the apex origin (``http://{DOMAIN}``) on pages served from a
    site's subdomain, where a relative link would stay on the site.
    """
    return to_xml(
        Html(
            Head(
                Meta(charset="utf-8"),
                Meta(name="viewport", content="width=device-width, initial-scale=1"),
                Title(title),
                Style(_TOKENS_CSS + _PAGE_CSS),
                Script(_THEME_BOOT_JS),
            ),
            Body(
                Main(
                    _header_bar(base),
                    *content,
                    _footer(base),
                    Script(_THEME_TOGGLE_JS),
                )
            ),
            lang="en",
        )
    )


def page(title: str, *content, status: int = 200) -> HTMLResponse:
    """A whole page as a response, for the website's routes."""
    return HTMLResponse(document(title, *content), status_code=status)


def hero(status_line: str, heading: str, intro: str):
    """The page opening: amber status line, title, muted introduction."""
    return (
        P(status_line, cls="status"),
        H1(heading),
        P(intro, cls="intro"),
    )


def section(label: str, *children, anchor: str | None = None):
    """A ruled section under an uppercase letterspaced label."""
    attrs = {"id": anchor} if anchor else {}
    return Section(H2(label), *children, **attrs)


def receipt(*rows):
    """Rows of dotted-leader key/value pairs."""
    return Dl(*rows, cls="receipt")


def receipt_row(key: str, value):
    return Div(Dt(key), Span(cls="leader"), Dd(value))


def endpoint_chip(url: str):
    """The MCP endpoint, framed like a snippet to copy."""
    return Div(A(url, href=url), cls="endpoint")


def command(text: str):
    """A shell command, framed to be copied — it scrolls rather than wraps."""
    return Pre(Code(text), cls="command")


def prompts(*texts: str):
    """Example things to say to an AI, carets and all."""
    return Div(
        *(Div(Span(">", cls="caret"), Span(text, cls="prompt-text")) for text in texts),
        cls="prompts",
    )


def _header_bar(base: str):
    return Header(
        A("cervo", href=f"{base}/", cls="wordmark"),
        Span(
            Span("static hosting", cls="tagline"),
            Button(id="theme-toggle", type="button", aria_label="Toggle color theme"),
            cls="header-right",
        ),
    )


def _footer(base: str):
    return Section(
        H2("ABOUT CERVO"),
        P(
            A("cervo", href="https://github.com/ericovis/cervo"),
            " is a demo app for managing static website hosting on a shared "
            "VPS, created by Eric Magalhães a.k.a. ",
            A("ericovis", href="https://github.com/ericovis"),
            ".",
        ),
        Div(
            A("docs", href=f"{base}/docs"),
            A("terms", href=f"{base}/terms"),
            A("privacy", href=f"{base}/privacy"),
            A("source", href="https://github.com/ericovis/cervo"),
            cls="footer-links",
        ),
        cls="fine",
    )
