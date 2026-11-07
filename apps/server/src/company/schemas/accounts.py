import re
import string
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class EmailAddress(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if not re.fullmatch(
            r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", value
        ):
            raise ValueError("Supply a valid email address.")
        return value


class AccountLogin(EmailAddress):
    # Existing passwords remain usable at login; the new policy applies to registration.
    password: SecretStr = Field(min_length=1, max_length=128)


class AccountRegistration(AccountLogin):
    password: SecretStr = Field(min_length=8, max_length=12)
    display_name: str = Field(min_length=1, max_length=200)
    verification_code: SecretStr = Field(min_length=6, max_length=6)

    @field_validator("password")
    @classmethod
    def password_policy(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if any(c not in string.ascii_letters + string.digits + string.punctuation for c in raw):
            raise ValueError("Use ASCII letters, digits and punctuation only.")
        groups = [
            any(c in group for c in raw)
            for group in (string.ascii_letters, string.digits, string.punctuation)
        ]
        if sum(groups) < 2:
            raise ValueError("Password needs at least two of letters, digits and punctuation.")
        return value

    @field_validator("verification_code")
    @classmethod
    def code_format(cls, value: SecretStr) -> SecretStr:
        if not re.fullmatch(r"[0-9]{6}", value.get_secret_value()):
            raise ValueError("Supply the six-digit email code.")
        return value

    @field_validator("display_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Display name cannot be blank.")
        return value.strip()


class CodeDeliveryView(BaseModel):
    expires_in_seconds: int = 600
    retry_after_seconds: int = 60


class AccountSessionView(BaseModel):
    id: UUID
    email: str
    display_name: str
    csrf_token: str
    expires_at: datetime


class AccountOrderView(BaseModel):
    id: UUID
    product_name: str
    phase: str
    is_demo: bool
    created_at: datetime
    updated_at: datetime


class AccountOrderList(BaseModel):
    items: list[AccountOrderView]
    total: int
    limit: int
    offset: int
