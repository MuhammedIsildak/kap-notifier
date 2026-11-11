from unittest.mock import patch

import pytest

from src.config import EmailConfig
from src.mailer import MailerError, send_email
from src.render import RenderedEmail

RENDERED = RenderedEmail(
    subject="KAP | test",
    html="<p>merhaba</p>",
    text="merhaba",
)


def test_missing_config_raises():
    with pytest.raises(MailerError):
        send_email(EmailConfig(), RENDERED)


def test_smtp_ssl_login_and_send():
    config = EmailConfig(
        smtp_user="gonderen@gmail.com",
        smtp_pass="apppassword",
        from_addr="gonderen@gmail.com",
        recipients=("alici@gmail.com",),
    )
    with patch("smtplib.SMTP_SSL") as smtp_ssl:
        server = smtp_ssl.return_value.__enter__.return_value
        send_email(config, RENDERED)

    smtp_ssl.assert_called_once_with("smtp.gmail.com", 465, timeout=30)
    server.login.assert_called_once_with("gonderen@gmail.com", "apppassword")
    server.send_message.assert_called_once()