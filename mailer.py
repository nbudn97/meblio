"""Email delivery for Meblio: real SMTP when configured, console/dev fallback otherwise."""
import os
import smtplib
from email.mime.text import MIMEText

from logger import get_logger

logger = get_logger("mail")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
FROM_EMAIL = os.environ.get("SMTP_FROM", "noreply@meblio.local")


def send_email(to_email, subject, body_text, link_url=None):
    """Deliver an email. Returns True if actually sent via SMTP, False if mocked."""
    if link_url:
        body_text = f"{body_text}\n\n{link_url}\n"
    if SMTP_HOST:
        try:
            msg = MIMEText(body_text, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = FROM_EMAIL
            msg["To"] = to_email
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.starttls()
                if SMTP_USER:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
            logger.info("email sent to %s: %s", to_email, subject)
            return True
        except Exception as exc:
            logger.exception("SMTP send failed to %s", to_email)
            logger.warning("falling back to mock email for %s (%s)", to_email, exc)
    else:
        logger.info("[MOCK EMAIL] to=%s subject=%s body=%s", to_email, subject, body_text.replace("\n", " "))
    return False
