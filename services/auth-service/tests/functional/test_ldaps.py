# services/auth-service/tests/functional/test_ldaps.py

"""Проверяет LDAPS-адаптер без сети и без настоящего корпоративного пароля."""

import asyncio
import ssl
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pdrd_auth_service.application.ports.credentials import (
    CorporateDirectoryUnavailable,
    InvalidCorporateCredentials,
)
from pdrd_auth_service.infrastructure import ldaps
from pdrd_auth_service.infrastructure.ldaps import (
    LdapsConnectionConfig,
    LdapsCredentialVerifier,
)

_GUID = UUID("26c879a8-dfa0-4a19-961b-f60de1ed15f1")


def _entry() -> dict[str, object]:
    """Создаёт ответ каталога с корректным стабильным идентификатором."""
    return {
        "type": "searchResEntry",
        "attributes": {
            "objectGUID": str(_GUID),
            "userAccountControl": 512,
            "sAMAccountName": "i.mein",
            "displayName": "Иван Мейн",
            "mail": "ivan@example.org",
        },
        "raw_attributes": {"objectGUID": [_GUID.bytes_le]},
    }


@dataclass
class _FakeConnection:
    """Имитирует результат bind и поиска, фиксируя параметры запросов."""

    response: list[dict[str, object]] = field(default_factory=lambda: [_entry()])
    result: dict[str, int] = field(default_factory=lambda: {"result": 0})
    bind_ok: bool = True
    search_ok: bool = True
    bind_error: Exception | None = None
    search_filter: str | None = None
    search_kwargs: dict[str, object] = field(default_factory=dict)
    unbound: bool = False
    search_count: int = 0

    def bind(self) -> bool:
        """Возвращает успешный bind либо имитирует транспортный сбой."""
        if self.bind_error is not None:
            raise self.bind_error
        return self.bind_ok

    def search(self, **kwargs: object) -> bool:
        """Запоминает точный фильтр и ограничение размера выдачи."""
        self.search_count += 1
        self.search_kwargs = kwargs
        self.search_filter = str(kwargs["search_filter"])
        return self.search_ok

    def unbind(self) -> None:
        """Подтверждает закрытие соединения даже при ошибке."""
        self.unbound = True


@dataclass
class _FakeLdap:
    """Принимает вызовы ldap3 и позволяет проверить защитные параметры."""

    connection: _FakeConnection
    tls_kwargs: dict[str, object] = field(default_factory=dict)
    server_kwargs: dict[str, object] = field(default_factory=dict)
    connection_kwargs: dict[str, object] = field(default_factory=dict)
    SIMPLE: str = "SIMPLE"
    SUBTREE: str = "SUBTREE"

    def Tls(self, **kwargs: object) -> object:
        """Запоминает обязательную проверку CA-сертификата."""
        self.tls_kwargs = kwargs
        return object()

    def Server(self, host: str, **kwargs: object) -> object:
        """Запоминает DNS-имя контроллера и режим SSL."""
        self.server_kwargs = {"host": host, **kwargs}
        return object()

    def Connection(self, server: object, **kwargs: object) -> _FakeConnection:
        """Запоминает principal, отключение referrals и пароль-заглушку."""
        self.connection_kwargs = {"server": server, **kwargs}
        return self.connection


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, ...]:
    """Создаёт настройку с тестовым CA-файлом и подменённый LDAP-клиент."""
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("offline fixture", encoding="utf-8")
    config = LdapsConnectionConfig(
        host="WIN-1L5FI1SGC9J.itcneoterm.local",
        port=636,
        base_dn="DC=itcneoterm,DC=local",
        domain="itcneoterm.local",
        ca_bundle_path=ca_bundle,
    )
    connection = _FakeConnection()
    fake_ldap = _FakeLdap(connection)
    monkeypatch.setattr(ldaps, "_load_ldap3", lambda: fake_ldap)
    return LdapsCredentialVerifier(config), connection, fake_ldap, config


def test_valid_bind_returns_guid_identity_and_enforces_tls(
    harness: tuple[Any, ...],
) -> None:
    """Использует objectGUID, сертификат CA и точный поиск после bind."""
    verifier, connection, fake_ldap, config = harness

    identity = asyncio.run(verifier.verify("i.mein", "example-test-password"))

    assert identity.provider_id == "active_directory"
    assert identity.namespace == "itcneoterm.local"
    assert identity.subject == str(_GUID)
    assert identity.login == "i.mein"
    assert identity.display_name == "Иван Мейн"
    assert identity.email == "ivan@example.org"
    assert fake_ldap.tls_kwargs == {
        "validate": ssl.CERT_REQUIRED,
        "ca_certs_file": str(config.ca_bundle_path),
    }
    assert fake_ldap.server_kwargs["host"] == config.host
    assert fake_ldap.server_kwargs["use_ssl"] is True
    assert fake_ldap.server_kwargs["port"] == 636
    assert fake_ldap.connection_kwargs["user"] == "i.mein@itcneoterm.local"
    assert fake_ldap.connection_kwargs["auto_referrals"] is False
    assert fake_ldap.connection_kwargs["auto_bind"] is False
    assert fake_ldap.connection_kwargs["read_only"] is True
    assert (
        fake_ldap.connection_kwargs["receive_timeout"] == config.receive_timeout_seconds
    )
    assert isinstance(fake_ldap.connection_kwargs["receive_timeout"], int)
    assert connection.search_filter == (
        "(&(objectCategory=person)(objectClass=user)(sAMAccountName=i.mein))"
    )
    assert connection.search_kwargs["search_base"] == config.base_dn
    assert connection.search_kwargs["size_limit"] == 2
    assert connection.unbound is True
    assert not hasattr(identity, "password")


def test_invalid_bind_rejects_login_and_closes_connection(
    harness: tuple[Any, ...],
) -> None:
    """Неправильный пароль не запускает поиск и не раскрывается в ошибке."""
    verifier, connection, _, _ = harness
    connection.bind_ok = False
    connection.result = {"result": 49}

    with pytest.raises(InvalidCorporateCredentials) as error:
        asyncio.run(verifier.verify("i.mein", "wrong-secret"))

    assert "wrong-secret" not in str(error.value)
    assert connection.search_count == 0
    assert connection.unbound is True


def test_empty_password_is_rejected_before_ldap_bind(
    harness: tuple[Any, ...],
) -> None:
    """Пустой пароль не может превратиться в анонимный LDAP-bind."""
    verifier, connection, _, _ = harness

    with pytest.raises(InvalidCorporateCredentials):
        asyncio.run(verifier.verify("i.mein", ""))

    assert connection.unbound is False
    assert connection.search_count == 0


@pytest.mark.parametrize("login", ["i.\nmein", "i.\x01mein", "i@other.local"])
def test_unsafe_login_is_rejected_before_ldap_bind(
    harness: tuple[Any, ...], login: str
) -> None:
    """Управляющие символы и чужой UPN не должны попадать в bind."""
    verifier, connection, fake_ldap, _ = harness

    with pytest.raises(InvalidCorporateCredentials):
        asyncio.run(verifier.verify(login, "example-test-password"))

    assert fake_ldap.connection_kwargs == {}
    assert connection.search_count == 0


def test_disabled_account_is_rejected(harness: tuple[Any, ...]) -> None:
    """Флаг ACCOUNTDISABLE запрещает вход даже после успешного bind."""
    verifier, connection, _, _ = harness
    entry = deepcopy(_entry())
    entry["attributes"]["userAccountControl"] = 514
    connection.response = [entry]

    with pytest.raises(InvalidCorporateCredentials):
        asyncio.run(verifier.verify("i.mein", "example-test-password"))

    assert connection.unbound is True


def test_search_reply_must_match_requested_login(harness: tuple[Any, ...]) -> None:
    """Каталог не может подменить sAMAccountName после точного поиска."""
    verifier, connection, _, _ = harness
    entry = deepcopy(_entry())
    entry["attributes"]["sAMAccountName"] = "another.user"
    connection.response = [entry]

    with pytest.raises(CorporateDirectoryUnavailable):
        asyncio.run(verifier.verify("i.mein", "example-test-password"))

    assert connection.unbound is True


def test_search_reply_allows_login_case_difference(harness: tuple[Any, ...]) -> None:
    """Регистр отображения sAMAccountName не меняет учётную запись."""
    verifier, connection, _, _ = harness
    entry = deepcopy(_entry())
    entry["attributes"]["sAMAccountName"] = "I.Mein"
    connection.response = [entry]

    identity = asyncio.run(verifier.verify("i.mein", "example-test-password"))

    assert identity.login == "I.Mein"


@pytest.mark.parametrize("entry_count", [0, 2])
def test_missing_or_ambiguous_user_is_rejected(
    harness: tuple[Any, ...], entry_count: int
) -> None:
    """Результат поиска обязан содержать ровно одну запись пользователя."""
    verifier, connection, _, _ = harness
    connection.response = [_entry() for _ in range(entry_count)]

    with pytest.raises(InvalidCorporateCredentials):
        asyncio.run(verifier.verify("i.mein", "example-test-password"))

    assert connection.unbound is True


@pytest.mark.parametrize("raw_guid", [[], [b"short"], ["not bytes"]])
def test_invalid_object_guid_fails_closed(
    harness: tuple[Any, ...], raw_guid: list[object]
) -> None:
    """При отсутствии корректного objectGUID не создаётся новая личность PDRD."""
    verifier, connection, _, _ = harness
    entry = deepcopy(_entry())
    entry["raw_attributes"]["objectGUID"] = raw_guid
    connection.response = [entry]

    with pytest.raises(CorporateDirectoryUnavailable):
        asyncio.run(verifier.verify("i.mein", "example-test-password"))

    assert connection.unbound is True


def test_transport_failure_is_generic_and_connection_is_closed(
    harness: tuple[Any, ...],
) -> None:
    """Ошибку сети не возвращают вместе с данными LDAP-клиента или паролем."""
    verifier, connection, _, _ = harness
    connection.bind_error = RuntimeError("network failure secret=example-test-password")

    with pytest.raises(CorporateDirectoryUnavailable) as error:
        asyncio.run(verifier.verify("i.mein", "example-test-password"))

    assert "example-test-password" not in str(error.value)
    assert connection.unbound is True


def test_search_failure_is_directory_error(harness: tuple[Any, ...]) -> None:
    """Недоступный поиск не трактуется как подтверждённая личность."""
    verifier, connection, _, _ = harness
    connection.search_ok = False

    with pytest.raises(CorporateDirectoryUnavailable):
        asyncio.run(verifier.verify("i.mein", "example-test-password"))

    assert connection.unbound is True


def test_missing_ldap_dependency_fails_closed(
    harness: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без LDAP-клиента вход не подтверждается и пароль не раскрывается."""
    verifier, connection, _, _ = harness

    def missing_client() -> object:
        """Имитирует отсутствие установленной runtime-зависимости."""
        raise ModuleNotFoundError("ldap3")

    monkeypatch.setattr(ldaps, "_load_ldap3", missing_client)

    with pytest.raises(CorporateDirectoryUnavailable) as error:
        asyncio.run(verifier.verify("i.mein", "example-test-password"))

    assert "example-test-password" not in str(error.value)
    assert connection.unbound is False


def test_noncredential_bind_error_is_directory_error(harness: tuple[Any, ...]) -> None:
    """Сбой сервера с кодом, отличным от 49, не считается неверным паролем."""
    verifier, connection, _, _ = harness
    connection.bind_ok = False
    connection.result = {"result": 52}

    with pytest.raises(CorporateDirectoryUnavailable):
        asyncio.run(verifier.verify("i.mein", "example-test-password"))

    assert connection.unbound is True


def test_receive_timeout_must_be_integer(tmp_path: Path) -> None:
    """Защищает ldap3 POSIX socket timeout от float, ломающего struct.pack."""
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("offline fixture", encoding="utf-8")

    with pytest.raises(ValueError, match="целым числом"):
        LdapsConnectionConfig(
            host="WIN-1L5FI1SGC9J.itcneoterm.local",
            port=636,
            base_dn="DC=itcneoterm,DC=local",
            domain="itcneoterm.local",
            ca_bundle_path=ca_bundle,
            receive_timeout_seconds=10.0,
        )


def test_filter_escaping_handles_metacharacters() -> None:
    """Введённый логин не может изменить смысл LDAP-фильтра."""
    assert ldaps._escape_filter_value("i*)(\\\x00.mein") == "i\\2a\\29\\28\\5c\\00.mein"


@pytest.mark.parametrize("host", ["192.168.10.5", "localhost", "ldaps://dc.local"])
def test_controller_must_have_dns_name(tmp_path: Path, host: str) -> None:
    """Вместо IP или URL требуется имя, проверяемое по сертификату."""
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("offline fixture", encoding="utf-8")

    with pytest.raises(ValueError, match=r"DNS-имя|IP-адрес"):
        LdapsConnectionConfig(
            host=host,
            port=636,
            base_dn="DC=itcneoterm,DC=local",
            domain="itcneoterm.local",
            ca_bundle_path=ca_bundle,
        )


def test_ca_file_is_required(tmp_path: Path) -> None:
    """Без доверенного CA-цепочки нельзя включить корпоративный вход."""
    with pytest.raises(ValueError, match="CA"):
        LdapsConnectionConfig(
            host="WIN-1L5FI1SGC9J.itcneoterm.local",
            port=636,
            base_dn="DC=itcneoterm,DC=local",
            domain="itcneoterm.local",
            ca_bundle_path=tmp_path / "missing.pem",
        )
