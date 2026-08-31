# services/api-gateway/tests/unit/test_broker_settings.py

"""Unit-тесты RabbitMQ settings и AMQP URL."""

from pdrd_api_gateway.core.settings import BrokerSettings
from pdrd_api_gateway.infrastructure.messaging.broker import (
    build_broker_url,
)


def test_broker_password_is_hidden_from_repr() -> None:
    """Проверяет отсутствие RabbitMQ password в repr."""
    settings = BrokerSettings(
        password="broker-secret-password",
    )

    assert "broker-secret-password" not in repr(
        settings,
    )


def test_broker_url_encodes_credentials_and_virtual_host() -> None:
    """Проверяет URL encoding специальных символов AMQP URL."""
    settings = BrokerSettings(
        host="rabbitmq",
        port=5672,
        user="pdrd@worker",
        password="secret/password",
        virtual_host="pdrd/test",
    )

    broker_url = build_broker_url(
        settings,
    )

    assert broker_url == (
        "amqp://pdrd%40worker:secret%2Fpassword@rabbitmq:5672/pdrd%2Ftest"
    )
