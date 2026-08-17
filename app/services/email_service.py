import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.models.email_outbox import EmailOutbox, OutboxStatus

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


def _send_via_resend(to: str, subject: str, text: str, html: str = None) -> None:
    """Raises on failure -- callers decide how to record that."""
    if not settings.resend_api_key:
        raise RuntimeError("resend_api_key not configured")

    payload = {"from": settings.email_from, "to": [to], "subject": subject, "text": text}
    if html:
        payload["html"] = html

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
            "User-Agent": "curl/8.0",  # Resend's Cloudflare front blocks the default urllib UA
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{exc.code} {exc.read().decode()}") from exc


async def queue_email(db: AsyncSession, to: str, subject: str, body: str, html: str = None) -> None:
    """Write the send intent to the outbox in the caller's own transaction.
    Does not commit -- the row becomes durable when the caller commits."""
    db.add(EmailOutbox(to_email=to, subject=subject, body=body, html_body=html))


async def dispatch_pending_emails(db: AsyncSession, limit: int = 20) -> None:
    """Deliver PENDING/retryable FAILED rows. Safe to call repeatedly/concurrently-ish;
    each row is committed right after its own send attempt so one bad row can't
    block the rest of the batch."""
    result = await db.execute(
        select(EmailOutbox)
        .where(EmailOutbox.status != OutboxStatus.SENT)
        .where(EmailOutbox.attempts < MAX_ATTEMPTS)
        .limit(limit)
    )
    rows = result.scalars().all()

    for row in rows:
        row.attempts += 1
        try:
            _send_via_resend(row.to_email, row.subject, row.body, row.html_body)
        except Exception as exc:
            row.status = OutboxStatus.FAILED
            row.last_error = str(exc)[:2000]
            logger.warning("Outbox send to %s failed (attempt %d): %s", row.to_email, row.attempts, exc)
        else:
            row.status = OutboxStatus.SENT
            row.sent_at = datetime.now(timezone.utc)
        await db.commit()
