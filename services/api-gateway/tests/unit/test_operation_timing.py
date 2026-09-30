# services/api-gateway/tests/unit/test_operation_timing.py

"""Регрессия timing: sync/async, success/error, один журнал без исходного текста."""

import logging

import pytest
from pdrd_api_gateway.core.observability import (
    configure_review_logging,
    log_execution_time,
)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("fail", [False, True])
async def test_single_redacted_timing_event(caplog, asynchronous, fail):
    """Проверяет возврат/исключение, метаданные и отсутствие чувствительных аргументов."""
    caplog.set_level(logging.INFO)

    def operation(secret):
        """Имитирует важную операцию с чувствительным аргументом."""
        if fail:
            raise LookupError("Expected")
        return len(secret)

    async def async_operation(secret):
        """Имитирует асинхронный сценарий с тем же контрактом."""
        return operation(secret)

    wrapped = log_execution_time(operation="review_request")(
        async_operation if asynchronous else operation
    )
    if fail:
        with pytest.raises(LookupError):
            if asynchronous:
                await wrapped("private-text-and-key")
            else:
                wrapped("private-text-and-key")
    else:
        value = (
            await wrapped("private-text-and-key")
            if asynchronous
            else wrapped("private-text-and-key")
        )
        assert value == 20
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.event == "operation_timing"
    assert record.operation == "review_request"
    assert record.status == ("error" if fail else "success")
    assert record.duration_ms >= 0
    assert record.exc_info is None
    assert "private-text-and-key" not in record.getMessage()


def test_runtime_logging_works_without_root_handler_and_is_not_duplicated(
    monkeypatch, capsys
):
    """Повторное создание приложения оставляет один видимый timing-обработчик."""
    logger = logging.Logger("isolated.review.timing")
    monkeypatch.setattr(logging, "getLogger", lambda *_: logger)
    configure_review_logging()
    configure_review_logging()
    logger.info(
        "event=operation_timing operation=review_request duration_ms=1 status=success"
    )
    assert len(logger.handlers) == 1
    assert not logger.propagate
    assert capsys.readouterr().err.count("event=operation_timing") == 1
