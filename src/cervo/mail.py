"""Outgoing mail.

In development this reaches mailcatcher, which accepts anything on
``EMAIL_HOST``/``EMAIL_PORT`` without auth or TLS and shows it at
http://localhost:1080 instead of delivering it.
"""

import smtplib
from email.message import EmailMessage

from cervo import config


def send(to: str, subject: str, body: str) -> None:
    """Send a plain-text email from ``EMAIL_FROM``."""
    message = EmailMessage()
    message["From"] = config.EMAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(config.EMAIL_HOST, config.EMAIL_PORT) as smtp:
        smtp.send_message(message)
