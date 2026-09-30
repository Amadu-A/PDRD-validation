# services/auth-service/src/pdrd_auth_service/infrastructure/ldaps.py

"""Проверяет корпоративный пароль и читает неизменяемую личность через LDAPS.

Соединение требует доверенный сертификат CA и DNS-имя контроллера. Пароль
передаётся только в bind и не входит в профиль PDRD или сообщения об ошибке.
"""

import asyncio
import importlib
import ipaddress
import ssl
import unicodedata
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from uuid import UUID

from pdrd_auth_service.application.ports.credentials import (
    CorporateDirectoryUnavailable,
    InvalidCorporateCredentials,
)
from pdrd_auth_service.domain.identity import CorporateIdentity

_ATTRIBUTES = (
    "objectGUID",
    "userAccountControl",
    "sAMAccountName",
    "displayName",
    "mail",
)
_ACCOUNT_DISABLED = 0x0002
_INVALID_CREDENTIALS_CODE = 49


@dataclass(frozen=True, slots=True)
class LdapsConnectionConfig:
    """Хранит несекретные параметры проверяемого LDAPS-соединения."""

    host: str
    port: int
    base_dn: str
    domain: str
    ca_bundle_path: Path
    connect_timeout_seconds: float = 5.0
    receive_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        """Отклоняет соединение без DNS-имени и доверенного CA-сертификата."""
        if (
            not isinstance(self.host, str)
            or not self.host
            or self.host != self.host.strip()
        ):
            raise ValueError("Требуется DNS-имя контроллера AD")
        if "." not in self.host or any(char in self.host for char in "/:@\\ "):
            raise ValueError("Требуется DNS-имя контроллера AD")
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            pass
        else:
            raise ValueError("IP-адрес не подходит для проверки имени сертификата AD")
        if not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("Неверный порт LDAPS")
        if not isinstance(self.base_dn, str) or not self.base_dn.strip():
            raise ValueError("Требуется base DN каталога")
        if not isinstance(self.domain, str) or not self.domain.strip():
            raise ValueError("Требуется домен AD")
        if (
            not isinstance(self.ca_bundle_path, Path)
            or not self.ca_bundle_path.is_file()
        ):
            raise ValueError("Требуется существующий файл доверенного CA")
        if self.connect_timeout_seconds <= 0 or self.receive_timeout_seconds <= 0:
            raise ValueError("Таймаут LDAPS должен быть положительным")


def _load_ldap3() -> ModuleType:
    """Загружает LDAP-клиент только при фактическом обращении к AD."""
    return importlib.import_module("ldap3")


def _escape_filter_value(value: str) -> str:
    """Экранирует спецсимволы RFC 4515 в значении поискового LDAP-фильтра."""
    escaped = {"\\": "\\5c", "*": "\\2a", "(": "\\28", ")": "\\29", "\x00": "\\00"}
    return "".join(escaped.get(char, char) for char in value)


def _one_value(attributes: dict[str, object], name: str) -> object | None:
    """Достаёт одно значение AD-атрибута и отвергает неоднозначные списки."""
    value = attributes.get(name)
    if isinstance(value, list | tuple):
        if len(value) != 1:
            return None
        return value[0]
    return value


def _required_text(attributes: dict[str, object], name: str) -> str:
    """Проверяет обязательный текстовый атрибут найденного пользователя."""
    value = _one_value(attributes, name)
    if not isinstance(value, str) or not value.strip():
        raise CorporateDirectoryUnavailable("Каталог AD вернул неполный профиль")
    return value


def _identity_from_entry(
    entry: dict[str, object], domain: str, requested_login: str
) -> CorporateIdentity:
    """Преобразует objectGUID из Windows-байтов в стабильный UUID PDRD."""
    attributes = entry.get("attributes")
    raw_attributes = entry.get("raw_attributes")
    if not isinstance(attributes, dict) or not isinstance(raw_attributes, dict):
        raise CorporateDirectoryUnavailable("Каталог AD вернул неполный профиль")

    raw_guid = _one_value(raw_attributes, "objectGUID")
    if not isinstance(raw_guid, bytes) or len(raw_guid) != 16:
        raise CorporateDirectoryUnavailable("Каталог AD вернул неполный профиль")

    raw_control = _one_value(attributes, "userAccountControl")
    try:
        control = int(raw_control) if raw_control is not None else None
    except (TypeError, ValueError) as exc:
        raise CorporateDirectoryUnavailable(
            "Каталог AD вернул неполный профиль"
        ) from exc
    if control is None:
        raise CorporateDirectoryUnavailable("Каталог AD вернул неполный профиль")
    if control & _ACCOUNT_DISABLED:
        raise InvalidCorporateCredentials("Неверные учётные данные")

    login = _required_text(attributes, "sAMAccountName")
    if login.casefold() != requested_login.casefold():
        raise CorporateDirectoryUnavailable(
            "Каталог AD вернул несоответствующий профиль"
        )
    display_name = _one_value(attributes, "displayName")
    if not isinstance(display_name, str) or not display_name.strip():
        display_name = login
    email = _one_value(attributes, "mail")
    if not isinstance(email, str) or not email.strip():
        email = None
    return CorporateIdentity(
        provider_id="active_directory",
        namespace=domain.lower(),
        subject=str(UUID(bytes_le=raw_guid)),
        login=login,
        display_name=display_name,
        email=email,
    )


class LdapsCredentialVerifier:
    """Подтверждает пароль в AD и возвращает только публичные атрибуты личности."""

    def __init__(self, config: LdapsConnectionConfig) -> None:
        """Принимает уже проверенную конфигурацию LDAPS."""
        self._config = config

    async def verify(self, login: str, password: str) -> CorporateIdentity:
        """Выполняет блокирующий LDAP-вызов вне event loop сервиса."""
        if (
            not isinstance(login, str)
            or not login
            or login != login.strip()
            or any(char in login for char in "@\\\x00")
            or any(unicodedata.category(char) == "Cc" for char in login)
            or not isinstance(password, str)
            or not password
        ):
            raise InvalidCorporateCredentials("Неверные учётные данные")
        return await asyncio.to_thread(self._verify_sync, login, password)

    def _verify_sync(self, login: str, password: str) -> CorporateIdentity:
        """Делает simple bind, точный поиск пользователя и закрывает соединение."""
        connection = None
        try:
            ldap = _load_ldap3()
            tls = ldap.Tls(
                validate=ssl.CERT_REQUIRED,
                ca_certs_file=str(self._config.ca_bundle_path),
            )
            server = ldap.Server(
                self._config.host,
                port=self._config.port,
                use_ssl=True,
                tls=tls,
                connect_timeout=self._config.connect_timeout_seconds,
            )
            connection = ldap.Connection(
                server,
                user=f"{login}@{self._config.domain}",
                password=password,
                authentication=ldap.SIMPLE,
                auto_bind=False,
                auto_referrals=False,
                read_only=True,
                receive_timeout=self._config.receive_timeout_seconds,
            )
            if not connection.bind():
                code = connection.result.get("result")
                if code == _INVALID_CREDENTIALS_CODE:
                    raise InvalidCorporateCredentials("Неверные учётные данные")
                raise CorporateDirectoryUnavailable("Каталог AD недоступен")

            escaped_login = _escape_filter_value(login)
            found = connection.search(
                search_base=self._config.base_dn,
                search_filter=(
                    "(&(objectCategory=person)(objectClass=user)"
                    f"(sAMAccountName={escaped_login}))"
                ),
                search_scope=ldap.SUBTREE,
                attributes=list(_ATTRIBUTES),
                size_limit=2,
            )
            if not found:
                raise CorporateDirectoryUnavailable("Поиск пользователя AD не выполнен")
            entries = [
                entry
                for entry in connection.response
                if entry.get("type") == "searchResEntry"
            ]
            if len(entries) != 1:
                raise InvalidCorporateCredentials("Неверные учётные данные")
            return _identity_from_entry(entries[0], self._config.domain, login)
        except (InvalidCorporateCredentials, CorporateDirectoryUnavailable):
            raise
        except Exception:
            raise CorporateDirectoryUnavailable("Каталог AD недоступен") from None
        finally:
            if connection is not None:
                with suppress(Exception):
                    connection.unbind()
