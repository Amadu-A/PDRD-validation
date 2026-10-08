# services/api-gateway/tests/unit/test_equipment_wait.py

"""Жёсткое ожидание EQ, отмена и сохранение частичных результатов."""

import asyncio
from uuid import uuid4

import pytest
from pdrd_api_gateway.core.settings import EquipmentSearchSettings
from pdrd_api_gateway.infrastructure.equipment_search import EquipmentSearchClient


@pytest.mark.asyncio
async def test_hung_state_is_cancelled_and_partial_result_is_kept(monkeypatch) -> None:
    """Сетевой запрос не растягивает бюджет; итог модели остаётся доступен."""
    cancelled = []
    calls = 0

    async def state(self, job_id):
        """Первое чтение зависает, второе возвращает частичный итог."""
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.Event().wait()
        return {"status": "cancelled", "results": [{"identity": {"model": "KM20"}}]}

    async def cancel(self, job_id):
        """Фиксирует передачу отмены до повторного чтения."""
        cancelled.append(job_id)

    monkeypatch.setattr(EquipmentSearchClient, "state", state)
    monkeypatch.setattr(EquipmentSearchClient, "cancel", cancel)
    client = EquipmentSearchClient(
        EquipmentSearchSettings(wait_seconds=0.01, timeout_seconds=0.01)
    )
    job_id = uuid4()
    result = await asyncio.wait_for(client.wait(job_id), 0.5)
    assert cancelled == [job_id]
    assert result["status"] == "incomplete"
    assert result["results"][0]["identity"]["model"] == "KM20"


@pytest.mark.asyncio
async def test_timeout_cleanup_is_also_bounded(monkeypatch) -> None:
    """Недоступная отмена не подвешивает основную ветвь оркестратора."""

    async def state(self, job_id):
        """Возвращает ещё выполняемое задание."""
        return {"status": "running", "results": []}

    async def cancel(self, job_id):
        """Имитирует зависший внутренний HTTP cancel."""
        await asyncio.Event().wait()

    monkeypatch.setattr(EquipmentSearchClient, "state", state)
    monkeypatch.setattr(EquipmentSearchClient, "cancel", cancel)
    client = EquipmentSearchClient(
        EquipmentSearchSettings(wait_seconds=0.01, timeout_seconds=0.01)
    )
    result = await asyncio.wait_for(client.wait(uuid4()), 0.5)
    assert result["status"] == "incomplete" and result["results"] == []
