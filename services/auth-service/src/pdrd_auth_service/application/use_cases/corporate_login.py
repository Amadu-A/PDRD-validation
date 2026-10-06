# services/auth-service/src/pdrd_auth_service/application/use_cases/corporate_login.py

"""Сценарий корпоративного входа до создания профиля и сессии PDRD.

Сервис принимает короткий sAMAccountName и передаёт пароль только адаптеру
проверки. Создание профиля, роль и сессия относятся к следующим этапам.
"""

from pdrd_auth_service.application.ports.credentials import (
    CorporateCredentialsPort,
    InvalidCorporateCredentials,
)
from pdrd_auth_service.domain.identity import CorporateIdentity


class VerifyCorporateLogin:
    """Отклоняет опасный ввод до обращения к защищённому каталогу."""

    def __init__(self, credentials: CorporateCredentialsPort) -> None:
        """Получает порт проверки без знания LDAP-клиента."""
        self._credentials = credentials

    async def execute(self, *, login: str, password: str) -> CorporateIdentity:
        """Проверяет короткий логин и пароль без их сохранения в объекте."""
        if not isinstance(login, str) or not isinstance(password, str):
            raise InvalidCorporateCredentials("Неверные учётные данные")
        short_login = login.strip()
        if (
            not short_login
            or len(short_login) > 64
            or "@" in short_login
            or "\\" in short_login
            or any(ord(character) < 32 for character in login)
            or not password
        ):
            raise InvalidCorporateCredentials("Неверные учётные данные")
        return await self._credentials.verify(short_login, password)
