# services/api-gateway/src/pdrd_api_gateway/infrastructure/messaging/broker.py

"""RabbitMQ connection helpers и readiness adapter."""

import asyncio
from urllib.parse import quote

from kombu import Connection
from kombu.exceptions import OperationalError

from pdrd_api_gateway.core.settings import BrokerSettings


def build_broker_url(
    settings: BrokerSettings,
) -> str:
    """Создаёт безопасный AMQP URL для RabbitMQ."""
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


class RabbitMqReadinessProbe:
    """Проверяет возможность подключения к project RabbitMQ vhost."""

    def __init__(
        self,
        *,
        broker_url: str,
        connect_timeout_seconds: float,
        health_timeout_seconds: float,
    ) -> None:
        """Сохраняет параметры RabbitMQ readiness check."""
        self._broker_url = broker_url
        self._connect_timeout_seconds = connect_timeout_seconds
        self._health_timeout_seconds = health_timeout_seconds

    def _check_sync(self) -> bool:
        """Выполняет блокирующее AMQP-подключение в отдельном потоке."""
        connection = Connection(
            self._broker_url,
            connect_timeout=(self._connect_timeout_seconds),
        )

        try:
            connection.ensure_connection(
                max_retries=0,
            )

            return bool(
                connection.connected,
            )
        except (
            OSError,
            OperationalError,
        ):
            return False
        finally:
            connection.release()

    async def is_ready(self) -> bool:
        """Асинхронно проверяет RabbitMQ без блокировки event loop."""
        try:
            async with asyncio.timeout(
                self._health_timeout_seconds,
            ):
                return await asyncio.to_thread(
                    self._check_sync,
                )
        except TimeoutError:
            return False
