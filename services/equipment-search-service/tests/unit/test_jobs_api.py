# services/equipment-search-service/tests/unit/test_jobs_api.py

"""Проверка идемпотентности заданий и восстановления потока событий."""

import asyncio
from dataclasses import asdict
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pdrd_equipment_search_service.application.jobs import SearchJobService
from pdrd_equipment_search_service.core.settings import Settings
from pdrd_equipment_search_service.domain.equipment import (
    EquipmentIdentity,
    ResolveResult,
)
from pdrd_equipment_search_service.main import create_app


class FakeRepository:
    """Минимальный журнал с атомарным порядком для теста контракта."""

    def __init__(self) -> None:
        """Создаёт пустую историю."""
        self.jobs: dict = {}
        self.events: dict = {}

    async def create_or_get(self, job_id, identities, allow_unverified):
        """Не принимает повторный ID с другим телом."""
        if job_id in self.jobs:
            state = self.jobs[job_id]
            if (
                state["identities"] != identities
                or state["allow_unverified"] != allow_unverified
            ):
                raise ValueError("Конфликт параметров.")
            return state.copy()
        state = {
            "job_id": job_id,
            "status": "queued",
            "identities": identities,
            "allow_unverified": allow_unverified,
            "cancel_requested": False,
            "results": [],
            "warning": "",
            "event_sequence": 0,
        }
        self.jobs[job_id] = state
        self.events[job_id] = []
        return state.copy()

    async def get(self, job_id):
        """Читает снимок статуса."""
        state = self.jobs.get(job_id)
        return state.copy() if state else None

    async def claim(self, job_id):
        """Захватывает только ожидающее задание."""
        state = self.jobs[job_id]
        if state["status"] != "queued" or state["cancel_requested"]:
            return False
        state["status"] = "running"
        return True

    async def cancel(self, job_id):
        """Ставит долговечный флаг отмены."""
        state = self.jobs[job_id]
        if state["status"] not in {"queued", "running"}:
            return False
        state["cancel_requested"] = True
        await self.append_event(job_id, "cancellation_requested", "Отмена")
        return True

    async def is_cancelled(self, job_id):
        """Читает флаг отмены."""
        return self.jobs[job_id]["cancel_requested"]

    async def append_event(self, job_id, event_type, message):
        """Выдаёт номер события всего задания."""
        state = self.jobs[job_id]
        state["event_sequence"] += 1
        sequence = state["event_sequence"]
        self.events[job_id].append(
            {
                "sequence": sequence,
                "event_type": event_type,
                "message": message,
            }
        )
        return sequence

    async def events_after(self, job_id, sequence):
        """Воспроизводит историю с указанного номера."""
        return [event for event in self.events[job_id] if event["sequence"] > sequence]

    async def finish(self, job_id, status, results, warning=""):
        """Фиксирует частичный или полный итог."""
        self.jobs[job_id].update(status=status, results=results, warning=warning)


class FakeResolver:
    """Возвращает доказуемый результат с контролем вызовов."""

    def __init__(self) -> None:
        """Запоминает модели."""
        self.calls: list[str] = []

    async def execute(
        self,
        identity,
        *,
        allow_unverified=False,
        should_stop=None,
        vision_budget=None,
        search_budget=None,
    ):
        """Выполняет один поиск, если задание активно."""
        if should_stop and await should_stop():
            raise AssertionError("Поиск вызван после отмены.")
        self.calls.append(identity.model)
        return ResolveResult(identity, "not_found")


@pytest.mark.asyncio
async def test_job_deduplicates_models_and_replays_same_request() -> None:
    """Одна модель на разных листах приводит к одному разрешению."""
    repo = FakeRepository()
    resolver = FakeResolver()
    service = SearchJobService(repo, resolver)
    job_id = uuid4()
    identity = EquipmentIdentity("IEK", "KM20")
    await service.start(job_id, [identity, identity], False)
    await asyncio.gather(*service._tasks.values())
    state = await service.start(job_id, [identity, identity], False)
    assert resolver.calls == ["KM20"]
    assert state["status"] == "completed"
    assert len(state["results"]) == 1
    assert [event["sequence"] for event in repo.events[job_id]] == [1, 2, 3, 4]


def test_internal_api_auth_idempotency_and_sse_replay() -> None:
    """API защищён ключом, а Last-Event-ID не повторяет старые события."""
    repo = FakeRepository()
    resolver = FakeResolver()
    service = SearchJobService(repo, resolver)
    app = create_app(Settings(internal_key="test-secret"), service)
    job_id = uuid4()
    path = f"/internal/v1/equipment-search/jobs/{job_id}"
    headers = {"X-Internal-Key": "test-secret"}
    body = {"identities": [asdict(EquipmentIdentity("IEK", "KM20"))]}
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 403
        assert client.post(path, json=body, headers=headers).status_code == 200
        assert client.post(path, json=body, headers=headers).status_code == 200
        conflict = client.post(
            path,
            json={"identities": [{"manufacturer": "IEK", "model": "KM21"}]},
            headers=headers,
        )
        assert conflict.status_code == 409
        for _ in range(20):
            if client.get(path, headers=headers).json()["status"] == "completed":
                break
            import time

            time.sleep(0.01)
        assert resolver.calls == ["KM20"]
        response = client.get(
            path + "/events",
            headers={**headers, "Last-Event-ID": "2"},
        )
        assert response.status_code == 200
        assert "id: 1\n" not in response.text
        assert "id: 2\n" not in response.text
        assert "id: 3\n" in response.text
        assert "id: 4\n" in response.text


@pytest.mark.asyncio
async def test_cancel_queued_job_finishes_without_search() -> None:
    """Отмена в очереди фиксирует терминальное состояние и не ищет модель."""
    repo = FakeRepository()
    resolver = FakeResolver()
    service = SearchJobService(repo, resolver, max_concurrent=1)
    await service._semaphore.acquire()
    job_id = uuid4()
    await service.start(job_id, [EquipmentIdentity("IEK", "KM20")], False)

    assert await service.cancel(job_id) is True
    service._semaphore.release()
    await asyncio.gather(*service._tasks.values())

    assert (await repo.get(job_id))["status"] == "cancelled"
    assert resolver.calls == []
    assert repo.events[job_id][-1]["event_type"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_running_job_interrupts_current_request() -> None:
    """Активный поиск прерывается до новой модели и завершает SSE."""

    class SlowResolver:
        """Позволяет отменить запрос во время ожидания сетевого ответа."""

        def __init__(self) -> None:
            """Создаёт событие начала операции."""
            self.started = asyncio.Event()
            self.calls: list[str] = []

        async def execute(
            self,
            identity,
            *,
            allow_unverified=False,
            should_stop=None,
            vision_budget=None,
            search_budget=None,
        ):
            """Блокируется до отмены текущей задачи."""
            self.calls.append(identity.model)
            self.started.set()
            await asyncio.Event().wait()

    repo = FakeRepository()
    resolver = SlowResolver()
    service = SearchJobService(repo, resolver)
    job_id = uuid4()
    await service.start(
        job_id,
        [EquipmentIdentity("IEK", "KM20"), EquipmentIdentity("IEK", "KM21")],
        False,
    )
    await asyncio.wait_for(resolver.started.wait(), 1)

    assert await service.cancel(job_id) is True
    await asyncio.gather(*service._tasks.values(), return_exceptions=True)

    state = await repo.get(job_id)
    assert state["status"] == "cancelled"
    assert resolver.calls == ["KM20"]
    assert repo.events[job_id][-1]["event_type"] == "cancelled"


@pytest.mark.asyncio
async def test_job_shares_bounded_vision_budget_across_models() -> None:
    """Модели одного задания не получают отдельные лимиты адресного VLM."""

    class BudgetResolver:
        """Расходует переданный бюджет и фиксирует отказы."""

        def __init__(self):
            """Создаёт независимый журнал бюджета для теста."""
            self.claims = []
            self.budgets = []

        async def execute(
            self,
            identity,
            *,
            allow_unverified=False,
            should_stop=None,
            vision_budget=None,
            search_budget=None,
        ):
            """Разрешает не больше настроенного числа VLM вызовов."""
            self.budgets.append(vision_budget)
            accepted = vision_budget.claim()
            self.claims.append(accepted)
            return ResolveResult(
                identity,
                "not_found" if accepted else "incomplete",
                warning="" if accepted else "Бюджет VLM",
            )

    repo, resolver = FakeRepository(), BudgetResolver()
    service = SearchJobService(repo, resolver, max_vision_calls=1)
    job_id = uuid4()
    await service.start(
        job_id,
        [EquipmentIdentity("IEK", "KM20"), EquipmentIdentity("IEK", "LAY5")],
        False,
    )
    await asyncio.gather(*service._tasks.values())
    assert resolver.claims == [True, False]
    assert resolver.budgets[0] is resolver.budgets[1]
    assert (await repo.get(job_id))["status"] == "incomplete"
