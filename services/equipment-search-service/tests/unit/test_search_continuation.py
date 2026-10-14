# services/equipment-search-service/tests/unit/test_search_continuation.py

"""Найденные без паспорта модели получают ограниченное продолжение и честные метрики."""

import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pdrd_equipment_search_service.application.job_execution import run_search_job
from pdrd_equipment_search_service.domain.equipment import (
    EquipmentIdentity,
    ResolveResult,
)


@pytest.mark.parametrize("result_status", ["not_found", "incomplete"])
async def test_unresolved_job_is_resumable_with_two_downloads_per_run(result_status):
    """Отсутствие ссылок не закрывает возможность продолжения; квота не растёт бесконечно."""
    identity = EquipmentIdentity("Болид", "Сигнал-10")
    state = {
        "identities": [asdict(identity)],
        "results": [],
        "checkpoint": {},
        "resume_count": 0,
        "allow_unverified": False,
        "policy": {
            "query_limit": 3,
            "download_limit": 2,
            "vision_limit": 0,
            "downloads_per_model": 2,
            "queries_per_model": 3,
        },
    }

    async def execute(current, *, search_budget, **options):
        """Воспроизводит две неудачные загрузки и поиск без новых ссылок."""
        search_budget.claim_query()
        search_budget.claim_download()
        return ResolveResult(
            current, result_status, reason_code="document_not_found", retryable=True
        )

    async def checkpoint(job_id, results, progress):
        """Сохраняет завершённый снимок для явного следующего запуска."""
        state["results"] = results
        state["checkpoint"] = progress

    repository = SimpleNamespace(
        claim=AsyncMock(return_value=True),
        get=AsyncMock(return_value=state),
        is_cancelled=AsyncMock(return_value=False),
        append_event=AsyncMock(),
        checkpoint=AsyncMock(side_effect=checkpoint),
        finish=AsyncMock(),
    )
    service = SimpleNamespace(
        repository=repository,
        resolver=SimpleNamespace(execute=execute),
        _semaphore=asyncio.Semaphore(1),
        _active=set(),
        batch_size=10,
        max_seconds=10,
    )
    job_id = uuid4()
    await run_search_job(service, job_id)
    assert repository.finish.await_args.args[1] == "incomplete"
    first = state["results"][0]["metrics"].copy()
    assert (
        first["downloads"],
        first["downloads_in_run"],
        first["download_limit_per_run"],
    ) == (2, 2, 2)
    state["resume_count"] = 1
    await run_search_job(service, job_id)
    metrics = state["results"][0]["metrics"]
    assert metrics["downloads"] == 4
    assert metrics["downloads_in_run"] == metrics["download_limit_per_run"] == 2
    assert metrics["run_number"] == 2
    assert metrics["verified_documents"] == 0
