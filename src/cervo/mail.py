"""Outgoing mail.

In development this reaches mailcatcher, which accepts anything on
``EMAIL_HOST``/``EMAIL_PORT`` without auth or TLS and shows it at
http://localhost:1080 instead of delivering it. Setting ``EMAIL_USER``
switches to provider mode: STARTTLS, then login — the shape of a real
SMTP provider on port 587.
"""

import smtplib
import ssl
from email.message import EmailMessage

from cervo import config


def send(to: str, subject: str, body: str) -> None:
    """Send a plain-text email from ``EMAIL_FROM``."""
    message = EmailMessage()
    message["From"] = config.EMAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(config.EMAIL_HOST, config.EMAIL_PORT, timeout=10) as smtp:
        if config.EMAIL_USER:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(config.EMAIL_USER, config.EMAIL_PASSWORD)
        smtp.send_message(message)
