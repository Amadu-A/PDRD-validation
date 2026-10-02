"""Unit-тесты короткоживущего HMAC-доступа к подготовленному ТЗ."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pdrd_api_gateway.domain.technical_assignment_access import (
    TechnicalAssignmentCapability,
)


def test_token_binds_technical_assignment_id_and_expires() -> None:
    """Подмена UUID, подписи или времени не открывает чужое ТЗ."""
    capability = TechnicalAssignmentCapability("t" * 32)
    issued_at = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
    technical_assignment_id = uuid4()
    grant = capability.issue(technical_assignment_id, now=issued_at)

    assert grant.token.startswith("v1.")
    assert grant.expires_at == issued_at + timedelta(hours=24)
    assert capability.verify(technical_assignment_id, grant.token, now=issued_at)
    assert not capability.verify(uuid4(), grant.token, now=issued_at)
    assert not capability.verify(
        technical_assignment_id, grant.token + "x", now=issued_at
    )
    assert not capability.verify(
        technical_assignment_id, grant.token, now=grant.expires_at
    )
    assert not capability.verify(technical_assignment_id, None, now=issued_at)
    assert not capability.verify(technical_assignment_id, "x" * 200, now=issued_at)


def test_key_must_be_long_and_is_hidden_from_repr() -> None:
    """Тестовый короткий ключ и журналирование объекта не раскрывают секрет."""
    with pytest.raises(ValueError):
        TechnicalAssignmentCapability("short")
    capability = TechnicalAssignmentCapability("secret" * 8)
    assert "secret" not in repr(capability)
