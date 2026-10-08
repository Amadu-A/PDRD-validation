# services/equipment-search-service/tests/unit/test_observability.py

"""Проверяет timing EQ операций без утечки входных данных."""

import logging

import pytest
from pdrd_equipment_search_service.core.observability import log_execution_time


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_timing_preserves_result_exception_and_hides_arguments(
    caplog, fails
) -> None:
    """Декоратор пишет одно событие и не заменяет ошибку операции."""
    error = ValueError("private-error")

    @log_execution_time(operation="equipment.test")
    async def operation(secret: str) -> str:
        """Возвращает вход либо выбрасывает тот же объект ошибки."""
        if fails:
            raise error
        return secret

    with caplog.at_level(logging.INFO):
        if fails:
            with pytest.raises(ValueError) as caught:
                await operation("private-input")
            assert caught.value is error
        else:
            assert await operation("private-input") == "private-input"
    records = [
        r for r in caplog.records if getattr(r, "event", None) == "operation_timing"
    ]
    assert len(records) == 1
    assert records[0].status == ("error" if fails else "success")
    assert records[0].duration_ms >= 0
    assert "private" not in caplog.text
