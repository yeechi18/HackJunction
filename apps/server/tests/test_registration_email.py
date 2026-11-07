"""Registration policy and TLS email adapter, without any external mail delivery."""

import smtplib

import pytest
from pydantic import SecretStr, ValidationError

from company.config import Settings
from company.schemas.accounts import AccountRegistration
from company.services.registration_email import deliver_registration_code


@pytest.mark.parametrize(
    "password",
    [
        "Abc12345",
        "Abc123456789",
        "Ab123456789!x",
        "Ab123456789!xyzABC",
        "abcdefgh!",
        "1234567!",
        "ABC123!@",
    ],
)
def test_password_accepts_8_to_18_characters_and_two_categories(password):
    registration = AccountRegistration(
        email="test@example.invalid",
        display_name="Test",
        password=password,
        verification_code="123456",
    )
    assert registration.password.get_secret_value() == password


@pytest.mark.parametrize(
    "password",
    [
        "Ab12!",
        "Ab123456789!xyzABCD",
        "abcdefgh",
        "12345678",
        "!@#$%^&*",
        "Abcd 123",
        "测试Ab12345",
        "Abc1234\n",
    ],
)
def test_password_rejects_length_single_category_spaces_and_non_ascii(password):
    with pytest.raises(ValidationError):
        AccountRegistration(
            email="test@example.invalid",
            display_name="Test",
            password=password,
            verification_code="123456",
        )


def test_test_mail_cannot_be_enabled_in_development_or_production():
    for environment in ["development", "production"]:
        with pytest.raises(ValidationError, match="APP_ENV=test"):
            Settings(_env_file=None, app_env=environment, mail_mode="test")


def test_smtp_uses_tls_sender_and_authorization_code_without_logging(monkeypatch, capsys):
    calls = {}

    class SMTP:
        def __init__(self, host, port, *, timeout, context):
            calls.update(host=host, port=port, timeout=timeout, tls=context)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, username, password):
            calls.update(username=username, password=password)

        def send_message(self, message):
            calls["message"] = message

    monkeypatch.setattr("company.services.registration_email.smtplib.SMTP_SSL", SMTP)
    configuration = Settings(
        _env_file=None,
        smtp_password=SecretStr("test-only-authorization-marker"),
        smtp_username="sender@example.invalid",
        smtp_from_email="sender@example.invalid",
    )
    deliver_registration_code(configuration, "recipient@example.invalid", "042613")
    assert calls["host"] == "smtp.qq.com" and calls["port"] == 465
    assert calls["tls"].check_hostname
    assert calls["username"] == "sender@example.invalid"
    assert calls["password"] == "test-only-authorization-marker"
    assert calls["message"]["To"] == "recipient@example.invalid"
    assert "sender@example.invalid" in calls["message"]["From"]
    assert "042613" in calls["message"].get_content()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("failed_stage", ["connect", "login", "send", "quit"])
def test_smtp_failure_logs_only_stage_type_and_numeric_status(monkeypatch, caplog, failed_stage):
    marker = "PRIVATE-AUTH-CODE-EMAIL-AND-VERIFICATION-MARKER"

    def fail(stage):
        if stage == failed_stage:
            raise smtplib.SMTPResponseException(554, marker.encode())

    class SMTP:
        def __init__(self, *args, **kwargs):
            fail("connect")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            if args[0] is None:
                fail("quit")

        def login(self, *args):
            fail("login")

        def send_message(self, message):
            fail("send")

    monkeypatch.setattr("company.services.registration_email.smtplib.SMTP_SSL", SMTP)
    configuration = Settings(
        _env_file=None,
        smtp_password=SecretStr(marker),
        smtp_username="private-sender@example.invalid",
        smtp_from_email="private-sender@example.invalid",
    )
    with pytest.raises(smtplib.SMTPResponseException):
        deliver_registration_code(configuration, "private-recipient@example.invalid", "042613")
    assert f"stage={failed_stage} error_type=SMTPResponseException smtp_code=554" in caplog.text
    for private in [marker, "private-sender", "private-recipient", "042613"]:
        assert private not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_smtp_timeout_logs_without_private_exception_text(monkeypatch, caplog):
    def timeout(*args, **kwargs):
        raise TimeoutError("PRIVATE-SMTP-MARKER")

    monkeypatch.setattr("company.services.registration_email.smtplib.SMTP_SSL", timeout)
    configuration = Settings(
        _env_file=None,
        smtp_password=SecretStr("test-only-authorization-marker"),
        smtp_username="sender@example.invalid",
        smtp_from_email="sender@example.invalid",
    )
    with pytest.raises(TimeoutError):
        deliver_registration_code(configuration, "recipient@example.invalid", "042613")
    assert "stage=connect error_type=TimeoutError smtp_code=unavailable" in caplog.text
    assert "PRIVATE-SMTP-MARKER" not in caplog.text
