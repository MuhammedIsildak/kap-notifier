from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from .config import EmailConfig
from .render import RenderedEmail

LOGGER = logging.getLogger(__name__)


class MailerError(RuntimeError):
    pass


def send_email(config: EmailConfig, rendered: RenderedEmail) -> None:
    if not config.is_configured:
        missing = []
        if not config.smtp_user:
            missing.append("KAP_SMTP_USER")
        if not config.smtp_pass:
            missing.append("KAP_SMTP_PASS")
        if not config.recipients:
            missing.append("KAP_EMAIL_TO")
        raise MailerError(
            "E-posta ayarları eksik: " + ", ".join(missing)
        )

    message = EmailMessage()
    message["Subject"] = rendered.subject
    message["From"] = config.effective_from
    message["To"] = ", ".join(config.recipients)
    message.set_content(rendered.text)
    message.add_alternative(rendered.html, subtype="html")

    LOGGER.info(
        "E-posta gönderiliyor: %s -> %s",
        config.effective_from,
        ", ".join(config.recipients),
    )

    try:
        if config.smtp_port == 465:
            with smtplib.SMTP_SSL(
                config.smtp_host, config.smtp_port, timeout=30
            ) as server:
                server.login(config.smtp_user, config.smtp_pass)
                server.send_message(message)
        else:
            with smtplib.SMTP(
                config.smtp_host, config.smtp_port, timeout=30
            ) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(config.smtp_user, config.smtp_pass)
                server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailerError(f"E-posta gönderilemedi: {exc}") from exc

    LOGGER.info("E-posta gönderildi")