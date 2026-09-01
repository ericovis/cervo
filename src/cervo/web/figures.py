"""The docs' illustrations: inline SVG, drawn in the design system's tokens.

Screenshots would mean binary assets, an external request, and a picture
that goes stale the day either interface moves a button. These are drawings
instead — the same shapes, in cervo's own colours, from the same theme
variables as the rest of the page, so they follow the light/dark toggle and
travel inside the HTML like everything else here.
"""

from html import escape

from fasthtml.common import Figcaption, Figure, NotStr

# Sizes are user units of the viewBox, which is 640 wide — near enough to
# the 640px content column that they read as pixels.
_BG = "var(--bg)"
_PANEL = "var(--code-bg)"
_RULE = "var(--rule)"
_INK = "var(--ink)"
_TEXT = "var(--text)"
_MUTED = "var(--muted)"
_ACCENT = "var(--accent)"


def _box(x, y, w, h, *, fill=_PANEL, stroke=_RULE, rx=8, width=1):
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>'
    )


def _text(x, y, s, *, fill=_TEXT, size=12, weight=400, anchor="start", spacing=0):
    return (
        f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" '
        f'font-weight="{weight}" text-anchor="{anchor}" '
        f'letter-spacing="{spacing}">{escape(s)}</text>'
    )


def _line(x1, y1, x2, y2, *, stroke=_RULE, width=1):
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
        f'stroke="{stroke}" stroke-width="{width}"/>'
    )


def _path(d, *, stroke, width=1.5, fill="none"):
    return (
        f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{width}" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )


def _circle(cx, cy, r, fill):
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}"/>'


def _arrow(x, y, *, length=26):
    """A short accent arrow pointing right, from ``x`` at height ``y``."""
    tip = x + length
    return _line(x, y, tip - 6, y, stroke=_ACCENT, width=1.5) + _path(
        f"M{tip - 9} {y - 4.5} L{tip} {y} L{tip - 9} {y + 4.5}", stroke=_ACCENT
    )


def _figure(title: str, height: int, body: str, caption: str):
    svg = (
        f'<svg viewBox="0 0 640 {height}" role="img" '
        f'aria-label="{escape(title)}" xmlns="http://www.w3.org/2000/svg">'
        f"<title>{escape(title)}</title>{body}</svg>"
    )
    return Figure(NotStr(svg), Figcaption(caption))


def connectors_screen():
    """Claude's settings, with the button that starts the whole thing."""
    body = [
        _box(0.5, 0.5, 639, 299, fill=_BG, rx=10),
        _line(0, 40, 640, 40),
        *(_circle(cx, 20, 4.5, "var(--dotted)") for cx in (22, 38, 54)),
        _text(78, 25, "claude.ai — Settings", fill=_MUTED, size=11.5),
        _line(176, 40, 176, 300),
        # The sidebar, with Connectors picked out.
        _text(28, 78, "Profile", fill=_MUTED, size=12),
        _box(14, 92, 148, 28, rx=6, stroke="none"),
        _text(28, 111, "Connectors", fill=_ACCENT, size=12, weight=600),
        _text(28, 144, "Data controls", fill=_MUTED, size=12),
        _text(28, 177, "Account", fill=_MUTED, size=12),
        # The list itself, and the button under it.
        _text(200, 78, "Connectors", fill=_INK, size=15, weight=600),
        _box(200, 96, 416, 42),
        _text(216, 122, "Google Drive", fill=_TEXT, size=12),
        _text(600, 122, "Connected", fill=_MUTED, size=11, anchor="end"),
        _box(200, 148, 416, 42),
        _text(216, 174, "Gmail", fill=_TEXT, size=12),
        _text(600, 174, "Connected", fill=_MUTED, size=11, anchor="end"),
        _box(200, 216, 236, 40, fill="none", stroke=_ACCENT, width=1.5),
        _text(318, 241, "+ Add custom connector", fill=_ACCENT, size=12.5, weight=600),
    ]
    return _figure(
        "Claude's settings, with Connectors selected and the "
        "Add custom connector button below the list",
        300,
        "".join(body),
        "Settings → Connectors → Add custom connector.",
    )


def connector_dialog(mcp_url: str):
    """The dialog itself: the address, and the two settings that matter."""
    body = [
        _box(0.5, 0.5, 639, 399, fill=_BG, rx=10),
        _text(28, 46, "Add custom connector", fill=_INK, size=15, weight=600),
        _text(28, 82, "NAME", fill=_MUTED, size=10, spacing=1.4),
        _box(28, 92, 584, 38),
        _text(44, 116, "cervo", fill=_INK, size=12.5),
        _text(28, 158, "REMOTE MCP SERVER URL", fill=_MUTED, size=10, spacing=1.4),
        _box(28, 168, 584, 38),
        _text(44, 192, mcp_url, fill=_INK, size=12.5),
        _line(28, 232, 612, 232),
        _text(28, 256, "ADVANCED SETTINGS", fill=_MUTED, size=10, spacing=1.4),
        # The hosted-metadata switch, on.
        _box(28, 272, 34, 20, rx=10, fill=_ACCENT, stroke=_ACCENT),
        _circle(52, 282, 7, _BG),
        _text(
            74,
            287,
            "Use Anthropic's hosted client metadata",
            fill=_INK,
            size=12.5,
        ),
        _text(28, 322, "AUTHENTICATION", fill=_MUTED, size=10, spacing=1.4),
        _box(28, 332, 240, 36),
        _text(44, 355, "Always required", fill=_INK, size=12.5),
        _path("M244 348 L250 354 L256 348", stroke=_MUTED),
        _text(452, 355, "Cancel", fill=_MUTED, size=12.5, anchor="end"),
        _box(496, 332, 116, 36, fill=_ACCENT, stroke=_ACCENT),
        _text(554, 355, "Connect", fill=_BG, size=12.5, weight=600, anchor="middle"),
    ]
    return _figure(
        "The Add custom connector dialog, with cervo's address filled in, "
        "hosted client metadata switched on, and authentication always required",
        400,
        "".join(body),
        "The name, the address, and the two settings under Advanced settings.",
    )


def verification_flow():
    """The sign-in, end to end: email, mailed code, code typed back."""
    body = [
        # 1 — cervo asks for an address.
        _box(0.5, 10, 189, 190, fill=_BG, rx=10),
        _text(18, 40, "● SIGN IN", fill=_ACCENT, size=9.5, spacing=1.2),
        _text(18, 62, "Connect to cervo", fill=_INK, size=13, weight=600),
        _text(18, 92, "YOUR EMAIL", fill=_MUTED, size=9, spacing=1.2),
        _box(18, 100, 154, 30, rx=6),
        _text(30, 120, "you@example.com", fill=_MUTED, size=10.5),
        _box(18, 144, 112, 30, rx=6, fill="none", stroke=_ACCENT),
        _text(74, 164, "Send the code", fill=_ACCENT, size=10.5, anchor="middle"),
        _arrow(196, 105),
        # 2 — the code lands in the inbox.
        _box(225.5, 10, 189, 190, fill=_BG, rx=10),
        _text(320, 40, "YOUR INBOX", fill=_MUTED, size=9, spacing=1.2, anchor="middle"),
        _box(280, 58, 80, 54, rx=6),
        _path("M283 62 L320 89 L357 62", stroke=_RULE),
        _text(
            320,
            134,
            "cervo verification code",
            fill=_MUTED,
            size=9.5,
            anchor="middle",
        ),
        _text(
            320,
            166,
            "483 921",
            fill=_ACCENT,
            size=22,
            weight=600,
            anchor="middle",
            spacing=1.5,
        ),
        _arrow(421, 105),
        # 3 — typed back, and the connector is live.
        _box(450.5, 10, 189, 190, fill=_BG, rx=10),
        _text(468, 40, "● CHECK YOUR INBOX", fill=_ACCENT, size=9.5, spacing=1.2),
        _text(468, 62, "Enter the code", fill=_INK, size=13, weight=600),
        _text(468, 92, "THE CODE", fill=_MUTED, size=9, spacing=1.2),
        _box(468, 100, 154, 30, rx=6),
        _text(480, 120, "483921", fill=_INK, size=11.5, spacing=2),
        _box(468, 144, 82, 30, rx=6, fill=_ACCENT, stroke=_ACCENT),
        _text(509, 164, "Sign in", fill=_BG, size=10.5, weight=600, anchor="middle"),
        # The captions under each panel.
        _text(95, 226, "1. Type your email", fill=_MUTED, size=11, anchor="middle"),
        _text(320, 226, "2. Read the code", fill=_MUTED, size=11, anchor="middle"),
        _text(545, 226, "3. Type it back", fill=_MUTED, size=11, anchor="middle"),
    ]
    return _figure(
        "The three steps of the sign-in: cervo's email form, the code "
        "arriving by mail, and the code typed back into cervo",
        240,
        "".join(body),
        "No password is involved — the address you verify owns your sites.",
    )
