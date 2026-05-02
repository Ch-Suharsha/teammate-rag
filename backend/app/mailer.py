from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Optional

from sqlalchemy.orm import Session

from .models import EmailLog
from .settings import get_settings

log = logging.getLogger(__name__)


def send_email(
    db: Session,
    *,
    to_address: str,
    subject: str,
    body: str,
    session_id: Optional[str] = None,
) -> EmailLog:
    settings = get_settings()
    record = EmailLog(
        session_id=session_id,
        to_address=to_address,
        subject=subject,
        body=body,
        status="queued",
    )
    db.add(record)
    db.flush()

    if not settings.smtp_host:
        record.status = "skipped_no_smtp"
        log.info("SMTP not configured; email logged but not sent: %s", record.id)
        return record

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
        record.status = "sent"
    except Exception as exc:
        log.exception("Failed to send email")
        record.status = "failed"
        record.error = str(exc)
    return record
