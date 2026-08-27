"""The sign-in pages: proving an email address, in the browser.

Connecting the cervo connector lands here — ``/authorize`` parks the OAuth
request as a transaction and redirects to ``/verify?txn=...``. The user
enters an email, a six-digit code is mailed, and typing it back finishes the
flow: the browser is redirected to Claude's callback and the chat is
connected. Wrong-code errors re-render the form; a dead transaction gets a
page telling the user to reconnect from Claude.
"""

from fasthtml.common import A, Button, Form, Input, P, Style
from pydantic import EmailStr, TypeAdapter, ValidationError
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from cervo import auth
from cervo.db import connect
from cervo.web import layout

_EMAIL = TypeAdapter(EmailStr)

_FORM_CSS = """
.verify { display: flex; flex-direction: column; gap: 14px; max-width: 420px; }
.verify label { font-size: 13px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted, inherit); }
.verify input { padding: 10px 14px; font: inherit; color: inherit; background: var(--code-bg); border: 1px solid var(--rule); border-radius: 6px; }
.verify input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
.verify button { padding: 10px 16px; font: inherit; font-weight: 600; cursor: pointer; color: inherit; background: transparent; border: 1px solid var(--accent); border-radius: 6px; }
.verify .error { color: var(--accent); }
"""


def verify_page(request: Request) -> Response:
    """The state of the sign-in: ask for an email, or for the mailed code."""
    txn_id = request.query_params.get("txn", "")
    with connect() as conn:
        txn = auth.transaction(conn, txn_id)
    if txn is None:
        return _gone_page()
    if txn.email is None or "change" in request.query_params:
        return _email_page(txn.txn_id, email=txn.email)
    return _code_page(txn.txn_id, txn.email)


async def submit_email(request: Request) -> Response:
    """Mail a code to the address the user typed, then show the code form."""
    form = await request.form()
    txn_id = str(form.get("txn", ""))
    address = str(form.get("email", "")).strip()

    try:
        address = _EMAIL.validate_python(address)
    except ValidationError:
        return _email_page(
            txn_id, email=address, error="That does not look like an email address."
        )

    try:
        with connect() as conn:
            auth.send_code(conn, txn_id, address)
    except auth.AuthError:
        return _gone_page()
    return RedirectResponse(f"/verify?txn={txn_id}", status_code=303)


async def submit_code(request: Request) -> Response:
    """Check the code; done means handing the browser back to Claude."""
    form = await request.form()
    txn_id = str(form.get("txn", ""))
    code = str(form.get("code", ""))

    with connect() as conn:
        txn = auth.transaction(conn, txn_id)
    if txn is None or txn.email is None:
        return _gone_page()

    # The connection closes before the error is rendered, so a refusal still
    # commits the attempt it recorded on the way (see cervo.errors.AppError).
    try:
        with connect() as conn:
            callback = auth.confirm(conn, txn_id, code)
    except auth.AuthError as error:
        if "attempts left" in str(error):
            return _code_page(txn_id, txn.email, error=str(error))
        return _gone_page(str(error))
    return RedirectResponse(callback, status_code=302)


def _email_page(
    txn_id: str, email: str | None = None, error: str | None = None
) -> HTMLResponse:
    return layout.page(
        "sign in — cervo",
        Style(_FORM_CSS),
        *layout.hero(
            "● SIGN IN",
            "Connect to cervo",
            "Claude sent you here to prove an email address. It will own "
            "everything you create, and a verification code goes there now.",
        ),
        layout.section(
            "YOUR EMAIL",
            *([P(error, cls="error")] if error else []),
            Form(
                Input(type="hidden", name="txn", value=txn_id),
                Input(
                    type="email",
                    name="email",
                    value=email or "",
                    placeholder="you@example.com",
                    required=True,
                    autofocus=True,
                ),
                Button("Send the code"),
                action="/verify/email",
                method="post",
                cls="verify",
            ),
        ),
    )


def _code_page(txn_id: str, email: str, error: str | None = None) -> HTMLResponse:
    return layout.page(
        "enter the code — cervo",
        Style(_FORM_CSS),
        *layout.hero(
            "● CHECK YOUR INBOX",
            "Enter the code",
            f"A six-digit code was sent to {email}. Type it here to finish connecting.",
        ),
        layout.section(
            "THE CODE",
            *([P(error, cls="error")] if error else []),
            Form(
                Input(type="hidden", name="txn", value=txn_id),
                Input(
                    type="text",
                    name="code",
                    inputmode="numeric",
                    pattern="[0-9]{6}",
                    placeholder="000000",
                    required=True,
                    autofocus=True,
                    autocomplete="one-time-code",
                ),
                Button("Sign in"),
                action="/verify/code",
                method="post",
                cls="verify",
            ),
            P(
                "Wrong address? ",
                A("Use a different one", href=f"/verify?txn={txn_id}&change=1"),
                ".",
            ),
        ),
    )


def _gone_page(reason: str | None = None) -> HTMLResponse:
    return layout.page(
        "sign-in expired — cervo",
        *layout.hero(
            "● EXPIRED",
            "This sign-in is over",
            reason or "This sign-in attempt has expired or was already completed.",
        ),
        layout.section(
            "WHAT NOW",
            P(
                "Go back to Claude and connect cervo again — a fresh sign-in "
                "starts from there."
            ),
        ),
        status=400,
    )
