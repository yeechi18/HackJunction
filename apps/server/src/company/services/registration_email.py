"""TLS email delivery; the test sink is explicitly unavailable outside APP_ENV=test."""

import hashlib
import hmac
import json
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

import certifi
from fastapi import HTTPException
from sqlalchemy import text

from company.config import Settings
from company.services.access import require_session_secret


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
    with smtplib.SMTP_SSL(
        settings.smtp_host,
        settings.smtp_port,
        timeout=settings.smtp_timeout_seconds,
        context=ssl.create_default_context(cafile=certifi.where()),
    ) as smtp:
        smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(message)
