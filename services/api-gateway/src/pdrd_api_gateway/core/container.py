# services/api-gateway/src/pdrd_api_gateway/core/container.py

"""Composition root микросервиса API Gateway.

Модуль хранит concrete runtime-зависимости приложения и является единственным
местом, где они собираются. Сейчас Gateway зависит только от конфигурации;
по мере развития сюда будут добавляться адаптеры PostgreSQL, очереди,
хранилища и orchestration.
"""

from dataclasses import dataclass

from pdrd_api_gateway.core.settings import Settings, get_settings


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Хранит concrete зависимости одного экземпляра API Gateway.

    Attributes:
        settings: Провалидированная runtime-конфигурация сервиса.
    """

    settings: Settings


def build_container() -> ApplicationContainer:
    """Собирает runtime-зависимости API Gateway.

    Returns:
        Готовый контейнер зависимостей для FastAPI-приложения.
    """
    return ApplicationContainer(
        settings=get_settings(),
    )
