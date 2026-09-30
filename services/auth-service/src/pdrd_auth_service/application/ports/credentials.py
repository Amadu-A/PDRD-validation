# services/auth-service/src/pdrd_auth_service/application/ports/credentials.py

"""Порт проверки корпоративного пароля без привязки к реализации LDAP."""

from typing import Protocol

from pdrd_auth_service.domain.identity import CorporateIdentity


class InvalidCorporateCredentials(PermissionError):
    """Учётные данные отклонены без раскрытия существования пользователя."""


class CorporateDirectoryUnavailable(RuntimeError):
    """Проверку нельзя завершить из-за недоступности или ошибки каталога."""


class CorporateCredentialsPort(Protocol):
    """Проверяет пароль в источнике идентификации и возвращает objectGUID."""

    async def verify(self, login: str, password: str) -> CorporateIdentity:
        """Возвращает личность только после успешной проверки учётных данных."""
        ...
