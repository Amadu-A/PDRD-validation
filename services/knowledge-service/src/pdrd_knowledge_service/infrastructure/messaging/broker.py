# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/messaging/broker.py

"""RabbitMQ connection helpers Knowledge Service."""

from urllib.parse import quote

from pdrd_knowledge_service.core.settings import (
    BrokerSettings,
)


def build_broker_url(
    settings: BrokerSettings,
) -> str:
    """Создаёт AMQP URL с безопасным encoding credentials."""
    encoded_user = quote(
        settings.user,
        safe="",
    )

    encoded_password = quote(
        settings.password.get_secret_value(),
        safe="",
    )

    encoded_virtual_host = quote(
        settings.virtual_host,
        safe="",
    )

    return (
        f"amqp://{encoded_user}:{encoded_password}"
        f"@{settings.host}:{settings.port}"
        f"/{encoded_virtual_host}"
    )
