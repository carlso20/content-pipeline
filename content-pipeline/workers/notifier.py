"""
workers/notifier.py
--------------------
Email notification worker for dark launch payloads and failure alerts.

Uses Gmail SMTP with an App Password (set GMAIL_APP_PASSWORD in Railway Variables).
Gmail App Passwords are generated at: myaccount.google.com/apppasswords

Two notification types:
  1. Dark launch review emails — sent when a Buzzsprout publish is simulated
  2. Failure alerts — sent when an episode exhausts all retries and hits the DLQ

If email credentials are not configured, notifications are logged only (non-fatal).
"""

import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import Config
from logger import get_logger

logger = get_logger(__name__)


def send_dark_launch_email(subject: str, body: str, brand_name: str) -> None:
    """
    Send a dark launch review email to Carl.
    Non-fatal if email credentials are not configured.
    """
    if not _email_configured():
        logger.warning(
            "Email not configured — dark launch payload logged only. "
            "Set NOTIFICATION_EMAIL_FROM, NOTIFICATION_EMAIL_TO, and "
            "GMAIL_APP_PASSWORD in Railway Variables to enable email delivery.",
            brand=brand_name,
            subject=subject,
        )
        logger.info("DARK LAUNCH PAYLOAD", body=body)
        return

    _send(
        subject=f"[Content Pipeline] {subject}",
        body=body,
    )
    logger.info(
        "Dark launch email sent",
        brand=brand_name,
        to=Config.NOTIFICATION_EMAIL_TO,
    )


def send_failure_alert(episode, brand, error: str) -> None:
    """
    Send a failure alert when an episode is moved to the dead letter queue.
    """
    subject = f"[FAILED] Episode processing failed — {episode.title or episode.youtube_video_id}"
    body = f"""PIPELINE FAILURE ALERT

An episode has exhausted all retries and been moved to the dead letter queue.

═══════════════════════════════════════
EPISODE DETAILS
═══════════════════════════════════════
Brand:          {brand.brand_name}
Title:          {episode.title}
YouTube URL:    {episode.youtube_url}
Episode ID:     {episode.episode_id}
Retry Count:    {episode.retry_count}
Failed At:      {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}

═══════════════════════════════════════
ERROR
═══════════════════════════════════════
{error}

═══════════════════════════════════════
NEXT STEPS
═══════════════════════════════════════
1. Review the error above
2. Fix the underlying issue (invalid YouTube URL, API quota, etc.)
3. In the database, reset the episode status to 'classified' and retry_count to 0
4. The pipeline will pick it up on the next poll cycle

Railway logs will have the full traceback for this episode.
"""

    if not _email_configured():
        logger.error(
            "Email not configured — failure alert logged only",
            episode_id=str(episode.episode_id),
            error=error,
        )
        return

    try:
        _send(subject=f"[Content Pipeline] {subject}", body=body)
        logger.info(
            "Failure alert email sent",
            episode_id=str(episode.episode_id),
            to=Config.NOTIFICATION_EMAIL_TO,
        )
    except Exception as exc:
        logger.error(
            "Could not send failure alert email",
            episode_id=str(episode.episode_id),
            error=str(exc),
        )


def _send(subject: str, body: str) -> None:
    """Send a plain-text email via Gmail SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = Config.NOTIFICATION_EMAIL_FROM
    msg["To"] = Config.NOTIFICATION_EMAIL_TO
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(Config.NOTIFICATION_EMAIL_FROM, Config.GMAIL_APP_PASSWORD)
        smtp.sendmail(
            Config.NOTIFICATION_EMAIL_FROM,
            Config.NOTIFICATION_EMAIL_TO,
            msg.as_string(),
        )


def _email_configured() -> bool:
    """Return True only if all three email credentials are set."""
    return bool(
        Config.NOTIFICATION_EMAIL_FROM
        and Config.NOTIFICATION_EMAIL_TO
        and Config.GMAIL_APP_PASSWORD
    )
