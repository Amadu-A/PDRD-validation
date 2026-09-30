# services/auth-service/src/pdrd_auth_service/core/settings.py

"""Настройки корпоративной аутентификации с обязательной проверкой TLS.

Общие параметры читаются из .env.example, затем переопределяются закрытым .env
и переменными процесса. До доставки доверенного сертификата AD сервис выключен.
"""

from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
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


class Settings(BaseSettings):
    """Не допускает включения Auth Service без доверенного CA и DNS-имени."""

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

    @model_validator(mode="after")
    def require_trusted_ldaps(self) -> "Settings":
        """Отклоняет рабочий запуск с IP, неверным FQDN или без читаемого CA."""
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

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Возвращает один проверенный снимок конфигурации на процесс."""
    return Settings()
