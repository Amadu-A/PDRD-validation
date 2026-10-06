# services/auth-service/src/pdrd_auth_service/infrastructure/smtp.py

"""Отправка подтверждения внешней регистрации через защищённый SMTP."""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from pdrd_auth_service.core.settings import EmailSettings


class VerificationDeliveryUnavailable(RuntimeError):
    """Письмо не отправлено; детали SMTP и пароль не возвращаются наружу."""


class SmtpVerificationEmail:
    """Строит ссылку во fragment, чтобы код не попал в HTTP access log."""

    def __init__(self, settings: EmailSettings) -> None:
        """Сохраняет конфигурацию без открытия подключения при старте."""
        self._settings = settings

    async def send_verification(self, *, recipient: str, token: str) -> None:
        """Выполняет синхронный SMTP-клиент вне event loop FastAPI."""
        try:
            await asyncio.to_thread(self._send, recipient, token)
        except (OSError, smtplib.SMTPException):
            raise VerificationDeliveryUnavailable(
                "Письмо временно недоступно"
            ) from None

    def _send(self, recipient: str, token: str) -> None:
        """Открывает SSL или StartTLS и отправляет одно письмо."""
        settings = self._settings
        message = EmailMessage()
        message["From"] = settings.from_email
        message["To"] = recipient
        message["Subject"] = "Подтверждение регистрации PDRD"
        link = (
            f"{settings.public_base_url.rstrip('/')}/account.html#verify_email={token}"
        )
        message.set_content(
            "Подтвердите адрес электронной почты для PDRD:\n"
            f"{link}\n\nСсылка действует 24 часа. "
            "Если вы не регистрировались, просто проигнорируйте письмо."
        )
        tls = ssl.create_default_context()
        if settings.use_ssl:
            with smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.timeout_seconds,
                context=tls,
            ) as client:
                client.login(
                    settings.smtp_user, settings.smtp_password.get_secret_value()
                )
                client.send_message(message)
        else:
            with smtplib.SMTP(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.timeout_seconds,
            ) as client:
                client.starttls(context=tls)
                client.login(
                    settings.smtp_user, settings.smtp_password.get_secret_value()
                )
                client.send_message(message)
