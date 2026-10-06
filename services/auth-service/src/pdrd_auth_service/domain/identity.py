# services/auth-service/src/pdrd_auth_service/domain/identity.py

"""Проверенная корпоративная идентичность для связывания с профилем PDRD.

Пароль сюда не попадает. Стабильным субъектом служит objectGUID, тогда как
логин, адрес почты и отображаемое имя могут изменяться в Active Directory.
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CorporateIdentity:
    """Хранит подтверждённую AD-личность без роли PDRD и учётных данных."""

    provider_id: str
    namespace: str
    subject: str
    login: str
    display_name: str
    email: str | None = None

    def __post_init__(self) -> None:
        """Отклоняет неполный ключ, который нельзя безопасно передать user-service."""
        if self.provider_id != "active_directory":
            raise ValueError("Неизвестный источник корпоративной идентичности")
        if not isinstance(self.namespace, str) or not self.namespace.strip():
            raise ValueError("Не задано пространство имён Active Directory")
        if self.namespace != self.namespace.strip().lower():
            raise ValueError("Домен Active Directory должен быть нормализован")
        if not isinstance(self.subject, str):
            raise TypeError("Субъект Active Directory должен быть строкой UUID")
        try:
            identifier = UUID(self.subject)
        except ValueError as error:
            raise ValueError("Субъект Active Directory должен быть UUID") from error
        if self.subject != str(identifier):
            raise ValueError("Субъект Active Directory должен быть каноническим UUID")
        for name, value in (
            ("login", self.login),
            ("display_name", self.display_name),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} не должен быть пустым")
        if self.email is not None and (
            not isinstance(self.email, str) or not self.email.strip()
        ):
            raise ValueError("email не должен быть пустым")

    @property
    def stable_key(self) -> tuple[str, str, str]:
        """Возвращает ключ для user-service, не зависящий от логина и email."""
        return (self.provider_id, self.namespace, self.subject)
