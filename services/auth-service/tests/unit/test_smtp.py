# services/auth-service/tests/unit/test_smtp.py

"""Проверяет защищённую доставку ссылки подтверждения без внешнего SMTP."""

import smtplib
from email.message import EmailMessage

import pytest
from pdrd_auth_service.core.settings import EmailSettings
from pdrd_auth_service.infrastructure import smtp
from pdrd_auth_service.infrastructure.smtp import (
    SmtpVerificationEmail,
    VerificationDeliveryUnavailable,
)
from pydantic import SecretStr


class FakeSmtp:
    """Запоминает транспорт, TLS, вход и отправленное письмо."""

    def __init__(self, *, ssl_from_start: bool) -> None:
        """Создаёт пустой почтовый транспорт для одного теста."""
        self.ssl_from_start = ssl_from_start
        self.started_tls = False
        self.login_used = False
        self.message: EmailMessage | None = None

    def __enter__(self) -> "FakeSmtp":
        """Имитирует контекстный менеджер smtplib."""
        return self

    def __exit__(self, *_: object) -> None:
        """Имитирует закрытие соединения."""

    def starttls(self, *, context: object) -> None:
        """Фиксирует обязательное TLS-обновление обычного SMTP."""
        assert context is not None
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        """Проверяет, что авторизация происходит после TLS."""
        assert self.ssl_from_start or self.started_tls
        assert user == "smtp@example.org"
        assert password == "test-smtp-secret"
        self.login_used = True

    def send_message(self, message: EmailMessage) -> None:
        """Запоминает письмо только после входа."""
        assert self.login_used
        self.message = message


@pytest.mark.parametrize("use_ssl", [True, False])
def test_verification_link_stays_in_fragment_and_smtp_uses_tls(
    monkeypatch: pytest.MonkeyPatch, use_ssl: bool
) -> None:
    """Код не уходит в query, а пароль SMTP не отправляется до TLS."""
    transport = FakeSmtp(ssl_from_start=use_ssl)

    def open_transport(*_: object, **kwargs: object) -> FakeSmtp:
        """Проверяет адрес и таймаут вместо открытия сети."""
        assert kwargs["timeout"] == 10
        return transport

    monkeypatch.setattr(smtp.smtplib, "SMTP_SSL" if use_ssl else "SMTP", open_transport)
    settings = EmailSettings(
        enabled=True,
        smtp_host="smtp.example.org",
        smtp_user="smtp@example.org",
        smtp_password=SecretStr("test-smtp-secret"),
        from_email="smtp@example.org",
        public_base_url="https://pdrd.example",
        use_ssl=use_ssl,
        use_starttls=not use_ssl,
    )
    SmtpVerificationEmail(settings)._send("person@example.org", "v" * 43)

    assert transport.message is not None
    assert transport.message["To"] == "person@example.org"
    assert (
        "https://pdrd.example/account.html#verify_email=" + "v" * 43
    ) in transport.message.get_content()
    assert "test-smtp-secret" not in transport.message.as_string()
    assert transport.started_tls is not use_ssl


@pytest.mark.asyncio
async def test_smtp_error_is_not_exposed_to_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ответ сервиса не содержит SMTP-адрес и служебный пароль."""
    sender = SmtpVerificationEmail(EmailSettings())

    def unavailable(*_: object) -> None:
        """Имитирует ошибку SMTP без действительной отправки."""
        raise smtplib.SMTPException("smtp-host smtp-secret")

    monkeypatch.setattr(sender, "_send", unavailable)
    with pytest.raises(VerificationDeliveryUnavailable) as error:
        await sender.send_verification(recipient="person@example.org", token="v" * 43)
    assert "smtp-host" not in str(error.value)
    assert "smtp-secret" not in str(error.value)
