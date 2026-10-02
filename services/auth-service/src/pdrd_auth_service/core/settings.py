# services/auth-service/src/pdrd_auth_service/core/settings.py

"""Настройки корпоративной аутентификации и серверных сессий.

Общие параметры читаются из .env.example, затем переопределяются закрытым .env
и переменными процесса. До доставки доверенного сертификата корпоративный
LDAPS-адаптер выключен, а email-вход и серверные сессии остаются независимыми.
"""

from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ActiveDirectorySettings(BaseModel):
    """Адрес и параметры LDAPS одного подтверждённого контроллера домена."""

    controller_host: str = Field(
        default="WIN-1L5FI1SGC9J.itcneoterm.local", min_length=1, max_length=253
    )
    port: int = Field(default=636, ge=1, le=65535)
    domain: str = Field(default="itcneoterm.local", min_length=1, max_length=253)
    base_dn: str = Field(default="DC=itcneoterm,DC=local", min_length=1)
    ca_bundle_path: str = ""
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    receive_timeout_seconds: float = Field(default=10.0, gt=0, le=120)


class DatabaseSettings(BaseModel):
    """Подключение только к собственной схеме сессий auth в PostgreSQL."""

    host: str = Field(default="postgres", min_length=1)
    port: int = Field(default=5432, ge=1, le=65535)
    name: str = Field(default="pdrd", min_length=1)
    user: str = Field(default="pdrd", min_length=1)
    password: SecretStr = SecretStr("change-me")
    pool_size: int = Field(default=5, ge=1, le=50)
    max_overflow: int = Field(default=10, ge=0, le=100)
    pool_timeout_seconds: float = Field(default=10, gt=0, le=120)
    connect_timeout_seconds: float = Field(default=5, gt=0, le=60)


class SessionSettings(BaseModel):
    """Сроки короткой сессии; 30-дневный remember-grant будет отдельным."""

    idle_timeout_seconds: int = Field(default=7200, ge=60, le=86400)
    absolute_timeout_seconds: int = Field(default=28800, ge=60, le=86400)

    @model_validator(mode="after")
    def require_consistent_timeouts(self) -> "SessionSettings":
        """Срок простоя не может превышать абсолютный предел."""
        if self.idle_timeout_seconds > self.absolute_timeout_seconds:
            raise ValueError("Срок простоя не может превышать срок сессии")
        return self


class HttpSettings(BaseModel):
    """Публичная граница сессии и закрытый обмен между микросервисами."""

    enabled: bool = False
    public_origin: str = "http://localhost:8080"
    cookie_secure: bool = True
    internal_key: SecretStr = SecretStr("")
    csrf_key: SecretStr = SecretStr("")
    user_service_url: str = "http://user-service:8000"
    user_service_internal_key: SecretStr = SecretStr("")
    request_timeout_seconds: float = Field(default=5, gt=0, le=30)


class EmailSettings(BaseModel):
    """SMTP с защищённым соединением и канонический адрес ссылки подтверждения."""

    enabled: bool = False
    smtp_host: str = "smtp.yandex.ru"
    smtp_port: int = Field(default=465, ge=1, le=65535)
    use_ssl: bool = True
    use_starttls: bool = False
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    from_email: str = ""
    public_base_url: str = ""
    timeout_seconds: float = Field(default=10, gt=0, le=60)


def _origin(value: str, *, require_https: bool) -> str:
    """Проверяет фиксированный origin без userinfo, пути и параметров."""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Требуется полный публичный origin без пути")
    if require_https and parsed.scheme != "https":
        raise ValueError("Публичный адрес должен использовать HTTPS")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("HTTP разрешён только для локального теста")
    return value.rstrip("/")


class Settings(BaseSettings):
    """Не допускает включения корпоративного входа без CA и DNS-имени."""

    model_config = SettingsConfigDict(
        env_file=(".env.example", ".env"),
        env_file_encoding="utf-8",
        env_prefix="AUTH_SERVICE_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["local", "dev", "test", "stage", "prod"] = "local"
    enabled: bool = False
    ad: ActiveDirectorySettings = Field(default_factory=ActiveDirectorySettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    sessions: SessionSettings = Field(default_factory=SessionSettings)
    http: HttpSettings = Field(default_factory=HttpSettings)
    email: EmailSettings = Field(default_factory=EmailSettings)

    @model_validator(mode="after")
    def require_trusted_ldaps(self) -> "Settings":
        """Отклоняет рабочий запуск с IP, неверным FQDN или без читаемого CA."""
        if self.http.enabled:
            if self.database.password.get_secret_value() in {"", "change-me"}:
                raise ValueError("Пароль PostgreSQL Auth Service не задан")
            for name, secret in (
                ("AUTH_SERVICE_HTTP__INTERNAL_KEY", self.http.internal_key),
                ("AUTH_SERVICE_HTTP__CSRF_KEY", self.http.csrf_key),
                (
                    "AUTH_SERVICE_HTTP__USER_SERVICE_INTERNAL_KEY",
                    self.http.user_service_internal_key,
                ),
            ):
                if len(secret.get_secret_value()) < 32:
                    raise ValueError(f"{name} должен содержать не менее 32 символов")
            _origin(
                self.http.public_origin,
                require_https=self.environment in {"stage", "prod"},
            )
            if self.environment in {"stage", "prod"} and not self.http.cookie_secure:
                raise ValueError("Публичный cookie должен быть Secure")

        if self.email.enabled:
            if not self.http.enabled:
                raise ValueError("Для SMTP нужен работающий HTTP Auth Service")
            if self.email.use_ssl == self.email.use_starttls:
                raise ValueError("Выберите ровно один способ защищённого SMTP")
            if not (
                self.email.smtp_user
                and self.email.smtp_password.get_secret_value()
                and self.email.from_email
            ):
                raise ValueError("SMTP реквизиты и адрес отправителя не заданы")
            if _origin(
                self.email.public_base_url,
                require_https=self.environment in {"stage", "prod"},
            ) != _origin(
                self.http.public_origin,
                require_https=self.environment in {"stage", "prod"},
            ):
                raise ValueError("Адрес письма должен совпадать с публичным origin")

        if not self.enabled:
            return self

        hostname = self.ad.controller_host
        labels = hostname.split(".")
        if len(labels) < 2 or any(
            not label
            or len(label) > 63
            or not label[0].isalnum()
            or not label[-1].isalnum()
            or any(
                not (character.isascii() and (character.isalnum() or character == "-"))
                for character in label
            )
            for label in labels
        ):
            raise ValueError("AUTH_SERVICE_AD__CONTROLLER_HOST должен быть DNS-именем")

        try:
            ip_address(hostname)
        except ValueError:
            pass
        else:
            raise ValueError(
                "AUTH_SERVICE_AD__CONTROLLER_HOST не должен быть IP-адресом"
            )

        ca_bundle = self.ad.ca_bundle_path.strip()
        if not ca_bundle:
            raise ValueError("AUTH_SERVICE_AD__CA_BUNDLE_PATH не задан")
        try:
            with Path(ca_bundle).open("rb") as certificate_file:
                certificate_file.read(1)
        except OSError as exc:
            raise ValueError(
                "AUTH_SERVICE_AD__CA_BUNDLE_PATH должен быть читаемым файлом"
            ) from exc

        if self.database.password.get_secret_value() in {"", "change-me"}:
            raise ValueError("Пароль PostgreSQL Auth Service не задан")

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Возвращает один проверенный снимок конфигурации на процесс."""
    return Settings()
