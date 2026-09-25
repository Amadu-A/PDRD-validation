# services/experience-service/src/pdrd_experience_service/core/settings.py

"""Типизированная конфигурация Experience Service.

Назначение файла:
- определять допустимые параметры сервиса и PostgreSQL;
- загружать общие значения из .env.example;
- применять приватные переопределения из .env;
- предоставлять переменным окружения наивысший приоритет;
- скрывать пароль при выводе объектов конфигурации;
- запрещать запуск активного сервиса с примерным паролем.

Этот модуль не создаёт SQLAlchemy engine и не открывает соединения.
За создание инфраструктурных зависимостей отвечает composition root.
"""

from functools import lru_cache
from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)

EnvironmentName = Literal[
    "local",
    "dev",
    "test",
    "stage",
    "prod",
]


class DatabaseSettings(BaseModel):
    """Параметры подключения к проектному PostgreSQL.

    Сервис использует PostgreSQL проекта PDRD, но владеет
    только собственной схемой experience и её миграциями.

    Пароль представлен типом SecretStr, чтобы случайный вывод
    Settings в журнал не раскрывал значение секрета.
    """

    host: str = Field(
        default="postgres",
        min_length=1,
    )

    port: int = Field(
        default=5432,
        ge=1,
        le=65535,
    )

    name: str = Field(
        default="pdrd",
        min_length=1,
    )

    user: str = Field(
        default="pdrd",
        min_length=1,
    )

    password: SecretStr = SecretStr(
        "change-me",
    )

    pool_size: int = Field(
        default=5,
        ge=1,
        le=50,
    )

    max_overflow: int = Field(
        default=10,
        ge=0,
        le=100,
    )

    pool_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
    )

    connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60,
    )


class Settings(BaseSettings):
    """Конфигурация сервиса с последовательным наложением источников.

    Порядок приоритетов от низшего к высшему:

    1. Значения по умолчанию в Python.
    2. Каталог параметров .env.example.
    3. Приватные переопределения .env.
    4. Переменные окружения текущего процесса.

    В Docker Compose оба файла передаются через env_file,
    поэтому конфигурация не зависит от наличия этих файлов
    внутри готового контейнера.

    Префикс EXPERIENCE_SERVICE_ исключает случайное смешивание
    настроек с API Gateway и другими микросервисами.
    """

    model_config = SettingsConfigDict(
        env_file=(
            ".env.example",
            ".env",
        ),
        env_file_encoding="utf-8",
        env_prefix="EXPERIENCE_SERVICE_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    environment: EnvironmentName = "local"

    # Пока полноценный HTTP-сервис не подключён,
    # новая функциональность остаётся выключенной.
    enabled: bool = False

    database: DatabaseSettings = Field(
        default_factory=DatabaseSettings,
    )

    @model_validator(
        mode="after",
    )
    def check_runtime_credentials(
        self,
    ) -> "Settings":
        """Блокирует запуск рабочего сервиса с фиктивным паролем.

        Локальные проверки выключенного сервиса могут использовать
        демонстрационную конфигурацию.

        Включённый сервис и production-окружение требуют
        настоящих учётных данных PostgreSQL.
        """
        password = self.database.password.get_secret_value()

        if (self.enabled or self.environment == "prod") and password in {
            "",
            "change-me",
            "CHANGE_ME",
        }:
            raise ValueError(
                "Для работающего Experience Service требуется "
                "настоящий пароль PostgreSQL."
            )

        return self


@lru_cache
def get_settings() -> Settings:
    """Возвращает кэш настроек для composition root.

    Повторное создание Settings при каждом запросе не требуется.
    Тесты могут создавать независимые объекты Settings напрямую.
    """
    return Settings()
