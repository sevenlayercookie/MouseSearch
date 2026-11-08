"""
Send-to-Kindle over email.

Amazon's "Send to Kindle" service accepts e-books as email attachments sent to
the user's @kindle.com address, provided the sender address is on the account's
"Approved Personal Document E-mail List". This module picks the best file from a
finished download and delivers it via SMTP using only the standard library.
"""

from __future__ import annotations

import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

# Formats Amazon's Send to Kindle email service accepts, best first.
# MOBI/AZW3 are no longer accepted by the service (since late 2022).
KINDLE_SUPPORTED_EXTENSIONS = (".epub", ".pdf", ".docx", ".doc", ".rtf", ".txt", ".htm", ".html")
KINDLE_MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
KINDLE_SMTP_SECURITY_MODES = ("starttls", "ssl", "none")


class KindleSendError(Exception):
    pass


def normalize_smtp_security(value) -> str:
    text = str(value or "").strip().lower()
    return text if text in KINDLE_SMTP_SECURITY_MODES else "starttls"


def parse_recipients(value) -> list[str]:
    """Split a comma/semicolon/whitespace separated list of addresses."""
    if isinstance(value, (list, tuple)):
        parts = value
    else:
        parts = str(value or "").replace(";", ",").replace("\n", ",").split(",")
    recipients = []
    for part in parts:
        address = str(part or "").strip()
        if address and address not in recipients:
            recipients.append(address)
    return recipients


def kindle_config_problem(config: dict) -> str | None:
    """Return a human-readable reason the email settings are unusable, or None."""
    if not str(config.get("KINDLE_SMTP_HOST") or "").strip():
        return "SMTP server is not set."
    if not parse_recipients(config.get("KINDLE_EMAIL")):
        return "Kindle email address is not set."
    if not str(config.get("KINDLE_FROM_EMAIL") or config.get("KINDLE_SMTP_USERNAME") or "").strip():
        return "Sender email address is not set."
    return None


def find_kindle_file(content_path: Path) -> Path | None:
    """
    Pick the best Kindle-compatible file from a torrent's content path.

    Prefers formats in KINDLE_SUPPORTED_EXTENSIONS order; within a format, the
    largest file wins (avoids sample/excerpt files bundled alongside the book).
    """
    content_path = Path(content_path)
    if content_path.is_file():
        candidates = [content_path]
    elif content_path.is_dir():
        candidates = [p for p in content_path.rglob("*") if p.is_file()]
    else:
        return None

    by_ext: dict[str, list[Path]] = {}
    for candidate in candidates:
        ext = candidate.suffix.lower()
        if ext in KINDLE_SUPPORTED_EXTENSIONS:
            by_ext.setdefault(ext, []).append(candidate)

    for ext in KINDLE_SUPPORTED_EXTENSIONS:
        files = by_ext.get(ext)
        if files:
            return max(files, key=lambda p: p.stat().st_size)
    return None


def build_kindle_message(
    *,
    sender: str,
    recipients: list[str],
    subject: str,
    attachment_name: str,
    attachment_bytes: bytes | None,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content("Sent by MouseSearch.")
    if attachment_bytes is not None:
        mime_type, _ = mimetypes.guess_type(attachment_name)
        if attachment_name.lower().endswith(".epub"):
            mime_type = "application/epub+zip"
        maintype, _, subtype = (mime_type or "application/octet-stream").partition("/")
        message.add_attachment(attachment_bytes, maintype=maintype, subtype=subtype, filename=attachment_name)
    return message


def send_message(config: dict, message: EmailMessage | None, timeout: float = 60.0) -> None:
    """Deliver a message with the configured SMTP server. Blocking; run in a thread."""
    host = str(config.get("KINDLE_SMTP_HOST") or "").strip()
    security = normalize_smtp_security(config.get("KINDLE_SMTP_SECURITY"))
    try:
        port = int(config.get("KINDLE_SMTP_PORT") or 0)
    except (TypeError, ValueError):
        port = 0
    if port <= 0:
        port = 465 if security == "ssl" else 587
    username = str(config.get("KINDLE_SMTP_USERNAME") or "").strip()
    password = str(config.get("KINDLE_SMTP_PASSWORD") or "")

    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
        with server:
            server.ehlo()
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if username:
                server.login(username, password)
            if message is not None:
                refused = server.send_message(message)
                if refused:
                    rejected = ", ".join(refused)
                    raise KindleSendError(
                        f"SMTP server rejected recipient(s): {rejected}. "
                        "Other recipients were accepted; resending to everyone may create duplicates. "
                        "Kindle delivery is not confirmed."
                    )
    except smtplib.SMTPAuthenticationError as exc:
        raise KindleSendError(f"SMTP login failed: {exc.smtp_error.decode(errors='replace') if isinstance(exc.smtp_error, bytes) else exc}") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise KindleSendError(f"SMTP error: {exc}") from exc


def send_file_to_kindle(config: dict, file_path: Path, *, title: str = "") -> str:
    """Email one e-book file to the configured Kindle address(es). Returns a status message."""
    problem = kindle_config_problem(config)
    if problem:
        raise KindleSendError(problem)

    file_path = Path(file_path)
    size = file_path.stat().st_size
    if size > KINDLE_MAX_ATTACHMENT_BYTES:
        raise KindleSendError(
            f"{file_path.name} is {size / 1024 / 1024:.1f} MB; Send to Kindle email accepts at most 50 MB."
        )

    recipients = parse_recipients(config.get("KINDLE_EMAIL"))
    sender = str(config.get("KINDLE_FROM_EMAIL") or config.get("KINDLE_SMTP_USERNAME") or "").strip()
    message = build_kindle_message(
        sender=sender,
        recipients=recipients,
        subject=title or file_path.stem,
        attachment_name=file_path.name,
        attachment_bytes=file_path.read_bytes(),
    )
    send_message(config, message)
    return f"SMTP server accepted {file_path.name} for {', '.join(recipients)}. Kindle delivery is not confirmed."


def test_connection(config: dict) -> str:
    """Check SMTP connection, TLS and authentication without sending an email."""
    if not str(config.get("KINDLE_SMTP_HOST") or "").strip():
        raise KindleSendError("SMTP server is not set.")
    send_message(config, None)
    return "SMTP connection successful. No email was sent; recipient acceptance and Kindle delivery were not tested."
