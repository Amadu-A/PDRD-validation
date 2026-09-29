# services/knowledge-service/tests/unit/test_experience_observability.py

"""Timing E сохраняет async-контракт, пишет одну запись и не раскрывает аргументы."""

import inspect
import logging

import pytest
from pdrd_knowledge_service.core.observability import (
    configure_experience_logging,
    log_execution_time,
)


@pytest.mark.parametrize("failure", [False, True])
async def test_timing_uses_monotonic_clock_and_keeps_result_or_original_exception(
    caplog, monkeypatch, failure
):
    """Один finally измеряет успех/сбой; traceback остаётся ответственному обработчику."""
    ticks = iter([10.0, 10.125])
    monkeypatch.setattr(
        "pdrd_knowledge_service.core.observability.perf_counter", lambda: next(ticks)
    )
    error = RuntimeError("own-error")

    @log_execution_time(operation="test_operation")
    async def operation(secret: str) -> str:
        """Проверяемая операция с чувствительным аргументом и неизменённой ошибкой."""
        if failure:
            raise error
        return secret

    assert str(inspect.signature(operation)) == "(secret: str) -> str"
    with caplog.at_level(logging.INFO, logger=__name__):
        if failure:
            with pytest.raises(RuntimeError) as caught:
                await operation("do-not-log-secret")
            assert caught.value is error
        else:
            assert await operation("do-not-log-secret") == "do-not-log-secret"
    records = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "operation_timing"
    ]
    assert len(records) == 1
    assert records[0].duration_ms == 125 and records[0].status == (
        "error" if failure else "success"
    )
    assert records[0].operation == "test_operation" and records[0].exc_info is None
    assert "do-not-log-secret" not in caplog.text


def test_http_logging_configuration_is_idempotent_and_does_not_reconfigure_root():
    """Повторное создание app не создаёт дубли handlers или бесконтрольные логи."""
    root_handlers = tuple(logging.getLogger().handlers)
    configure_experience_logging()
    logger = logging.getLogger(
        "pdrd_knowledge_service.application.use_cases.trusted_experience"
    )
    handlers = tuple(logger.handlers)
    configure_experience_logging()
    assert (
        tuple(logger.handlers) == handlers
        and tuple(logging.getLogger().handlers) == root_handlers
    )
    assert logger.level == logging.INFO and not logger.propagate
