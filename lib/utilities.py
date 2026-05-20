"""Shared utility helpers (currently: Gmail SMTP).

Ported from ~/projects/OpenAIAgentsSDK/utilities.py with light cleanup.
"""
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterable, Optional


_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587


def send_email(
    to_addresses: Iterable[str],
    subject: str,
    html_content: str,
    *,
    from_address: Optional[str] = None,
) -> None:
    """Send an HTML email via Gmail SMTP using STARTTLS.

    Reads GMAIL_USER (sender + login) and GMAIL_PASSWORD (Gmail app password)
    from the environment. Pass `from_address` to override the sender.
    """
    sender = from_address or os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_PASSWORD")
    if not sender:
        raise RuntimeError("GMAIL_USER not set in environment")
    if not password:
        raise RuntimeError("GMAIL_PASSWORD not set in environment")

    recipients = list(to_addresses)
    if not recipients:
        raise ValueError("send_email: at least one recipient required")

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, recipients, msg.as_string())


def send_gmail(subject: str, html_str: str, *, to: Optional[str] = None) -> None:
    """Send an HTML email to a single recipient (defaults to GMAIL_USER).

    Convenience wrapper around send_email for the common single-recipient case.
    """
    sender = os.environ.get("GMAIL_USER")
    recipient = to or sender
    if not recipient:
        raise RuntimeError("No recipient: pass `to=` or set GMAIL_USER")
    send_email([recipient], subject, html_str)
