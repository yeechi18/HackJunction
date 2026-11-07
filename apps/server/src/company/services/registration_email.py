"""TLS email delivery; the test sink is explicitly unavailable outside APP_ENV=test."""

import hashlib
import hmac
import json
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

import certifi
from fastapi import HTTPException
from sqlalchemy import text

from company.config import Settings
from company.services.access import require_session_secret

logger = logging.getLogger(__name__)


def require_mail(settings: Settings) -> None:
    if settings.mail_mode == "test" and settings.app_env == "test" and settings.mail_test_dir:
        return
    if settings.mail_mode != "smtp" or not all(
        [
            settings.smtp_password,
            settings.smtp_username,
            settings.smtp_from_email,
            settings.smtp_host,
        ]
    ):
        logger.warning("registration_email_not_configured")
        raise HTTPException(503, "Registration email delivery is not configured.")


def code_digest(settings: Settings, email: str, nonce: str, code: str) -> str:
    key = require_session_secret(settings)
    return hmac.new(
        key.encode(), f"registration:{email}:{nonce}:{code}".encode(), hashlib.sha256
    ).hexdigest()


async def lock_email(database, email: str) -> None:
    # Covers both the absent row and existing row, so concurrent sends/registrations serialize.
    key = int.from_bytes(hashlib.sha256(email.encode()).digest()[:8], "big", signed=True)
    await database.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def deliver_registration_code(settings: Settings, email: str, code: str) -> None:
    require_mail(settings)
    if settings.mail_mode == "test":
        directory = settings.mail_test_dir
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / (hashlib.sha256(email.encode()).hexdigest() + ".json")
        destination.write_text(json.dumps({"code": code}), encoding="utf-8")
        destination.chmod(0o600)
        return
    message = EmailMessage()
    message["Subject"] = "出海工坊 — 注册邮箱验证码"
    message["From"] = formataddr(("出海工坊", settings.smtp_from_email))
    message["To"] = email
    message.set_content(
        f"你正在注册出海工坊卖家账号。\n\n验证码：{code}\n\n10分钟内有效，请勿向他人透露。若不是你本人操作，请忽略此邮件。\n"
    )
    stage = "connect"
    try:
        with smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
            context=ssl.create_default_context(cafile=certifi.where()),
        ) as smtp:
            stage = "login"
            smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            stage = "send"
            smtp.send_message(message)
            stage = "quit"
    except Exception as error:
        # Never log exception text/tracebacks: SMTP replies can contain private data.
        smtp_code = getattr(error, "smtp_code", None)
        logger.warning(
            "registration_email_delivery_failed stage=%s error_type=%s smtp_code=%s",
            stage,
            type(error).__name__,
            smtp_code if isinstance(smtp_code, int) else "unavailable",
        )
        raise
