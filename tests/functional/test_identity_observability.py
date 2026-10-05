# tests/functional/test_identity_observability.py

"""Проверяет измерение длительности операций без раскрытия паролей и ошибок."""

import importlib
import logging

import pytest


@pytest.mark.parametrize("package", ["pdrd_auth_service", "pdrd_user_service"])
@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.asyncio
async def test_identity_timing_excludes_sensitive_values(package, fail, caplog):
    """Измерение сохраняет результат и исключение без записи их содержимого в лог."""
    module = importlib.import_module(f"{package}.core.observability")

    @module.log_execution_time(operation="test_identity")
    async def action(password):
        """Имитирует вызов с секретом в аргументе и тексте исключения."""
        if fail:
            raise PermissionError(password)
        return password

    with caplog.at_level(logging.INFO):
        if fail:
            with pytest.raises(PermissionError, match="private-password"):
                await action("private-password")
        else:
            assert await action("private-password") == "private-password"
    record = caplog.records[-1]
    assert record.event == "operation_timing"
    assert record.operation == "test_identity"
    assert record.status == ("error" if fail else "success")
    assert record.duration_ms >= 0
    assert "private-password" not in caplog.text


@pytest.mark.parametrize("package", ["pdrd_auth_service", "pdrd_user_service"])
def test_identity_logging_configuration_is_idempotent(package):
    """Повторная сборка runtime не добавляет дублирующие обработчики."""
    module = importlib.import_module(f"{package}.core.observability")
    logger = logging.getLogger(f"{package}.application")
    handlers, level, propagate = logger.handlers[:], logger.level, logger.propagate
    try:
        module.configure_identity_logging()
        module.configure_identity_logging()
        assert (
            sum(
                bool(getattr(handler, "pdrd_identity_timing", False))
                for handler in logger.handlers
            )
            == 1
        )
    finally:
        for handler in logger.handlers:
            if handler not in handlers:
                handler.close()
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate
