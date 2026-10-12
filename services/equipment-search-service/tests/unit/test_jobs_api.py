# services/equipment-search-service/tests/unit/test_jobs_api.py

"""Проверка идемпотентности заданий и восстановления потока событий."""

import asyncio
import json
from copy import deepcopy
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
        return deepcopy(state) if state else None

    async def configure(self, job_id, policy):
        """Закрепляет политику и начальную версию тестового задания."""
        state = self.jobs[job_id]
        state.setdefault("policy", policy)
        state.setdefault("checkpoint", {})
        state.setdefault("revision", 1)
        state.setdefault("resume_count", 0)
        state["background"] = state["policy"]["background"]
        return state.copy()

    async def checkpoint(self, job_id, results, checkpoint):
        """Воспроизводит JSON-снимок прогресса после попытки модели."""
        state = self.jobs[job_id]
        if len(state["results"]) != len(results):
            state["results"] = json.loads(json.dumps(results))
        index = int(
            (checkpoint.get("inflight") or {}).get(
                "index",
                checkpoint.get(
                    "model_index", max(0, int(checkpoint.get("cursor") or 0) - 1)
                ),
            )
        )
        if index < len(results):
            state["results"][index] = json.loads(json.dumps(results[index]))
        state["checkpoint"] = json.loads(json.dumps(checkpoint))
        state["revision"] += 1

    async def queued_jobs(self):
        """Читает ожидающие задания тестовой очереди."""
        return tuple(
            key for key, state in self.jobs.items() if state["status"] == "queued"
        )

    async def release(self, job_id):
        """Возвращает сохранённую порцию в долговечную очередь."""
        state = self.jobs[job_id]
        if state["cancel_requested"]:
            return False
        state["status"] = "queued"
        return True

    async def resume(self, job_id, revision, max_runs):
        """Проверяет версию и предел явных продолжений."""
        state = self.jobs.get(job_id)
        if (
            not state
            or state["revision"] != revision
            or state["status"] not in {"incomplete", "cancelled"}
            or state["resume_count"] >= max_runs
        ):
            return False
        state.update(
            status="queued",
            cancel_requested=False,
            revision=revision + 1,
            resume_count=state["resume_count"] + 1,
        )
        return True

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


class JsonbRepository(FakeRepository):
    """Воспроизводит преобразование JSONB и проверку запроса после INSERT."""

    async def create_or_get(self, job_id, identities, allow_unverified):
        """Возвращает списки из JSON и сравнивает их с исходной командой."""
        await super().create_or_get(job_id, identities, allow_unverified)
        state = self.jobs[job_id]
        state["identities"] = json.loads(json.dumps(state["identities"]))
        if state["identities"] != identities:
            raise ValueError("Задание с этим ID уже создано с другими параметрами.")
        return state.copy()


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
        round_index=None,
        previous=None,
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


@pytest.mark.asyncio
@pytest.mark.parametrize("properties", [(), ("voltage",), ("voltage", "power")])
@pytest.mark.parametrize("allow_unverified", [False, True])
async def test_job_payload_survives_jsonb_round_trip(properties, allow_unverified):
    """Пустые и непустые характеристики не вызывают конфликт нового задания."""
    repo, resolver = JsonbRepository(), FakeResolver()
    service = SearchJobService(repo, resolver)
    job_id = uuid4()
    identity = EquipmentIdentity("IEK", "KM20", properties=properties)
    try:
        first = await service.start(job_id, [identity], allow_unverified)
        assert first["identities"][0]["properties"] == list(properties)
        await asyncio.gather(*service._tasks.values())
        repeated = await service.start(job_id, [identity], allow_unverified)
        assert repeated["status"] == "completed"
        assert resolver.calls == ["KM20"]
        with pytest.raises(ValueError, match="Конфликт параметров"):
            await service.start(job_id, [identity], not allow_unverified)
    finally:
        await service.shutdown()


def test_internal_api_auth_idempotency_and_sse_replay() -> None:
    """API защищён ключом, а Last-Event-ID не повторяет старые события."""
    repo = JsonbRepository()
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
            round_index=None,
            previous=None,
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
            round_index=None,
            previous=None,
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


class AttemptResolver:
    """Воспроизводит отказ после одной загрузки, без внешних сети и GPU."""

    def __init__(self, stop_at=None):
        """Назначает модель аварийной остановки и журнал попыток."""
        self.calls = []
        self.stop_at = stop_at
        self.stopped = asyncio.Event()

    async def execute(
        self, identity, *, search_budget, round_index, vision_budget, **kwargs
    ):
        """Расходует операции до остановки, чтобы проверить аварийный резерв."""
        self.calls.append((round_index, identity.model))
        assert search_budget.claim_query()
        if round_index < 2:
            assert search_budget.claim_download()
        if self.stop_at == identity.model:
            self.stopped.set()
            await asyncio.Event().wait()
        return ResolveResult(
            identity,
            "incomplete",
            reason_code="model_mismatch",
            retryable=True,
            attempts=({"stage": "verify", "code": "model_mismatch"},),
        )


@pytest.mark.parametrize("count", [300, 500])
async def test_every_model_precedes_retries_and_budgets_are_bounded(count):
    """Все модели получают первый запрос раньше второго; GPU не масштабируется поштучно."""
    repository, resolver = FakeRepository(), AttemptResolver()
    service = SearchJobService(repository, resolver, max_vision_calls=0)
    job_id = uuid4()
    identities = [EquipmentIdentity("IEK", f"MODEL-{index}") for index in range(count)]
    created = await service.start(job_id, identities, True)
    await asyncio.gather(*service._tasks.values())
    assert len(resolver.calls) == service.batch_size
    while (await repository.get(job_id))["status"] == "queued":
        await service._run(job_id)
    state = await repository.get(job_id)
    assert created["background"]
    assert resolver.calls[:count] == [(0, item.model) for item in identities]
    assert resolver.calls[count : 2 * count] == [(1, item.model) for item in identities]
    assert len(state["results"]) == count
    assert sum(row["metrics"]["queries"] for row in state["results"]) == 3 * count
    assert sum(row["metrics"]["downloads"] for row in state["results"]) == 2 * count
    assert state["checkpoint"]["queries_used"] == 3 * count
    assert state["policy"]["vision_limit"] == 0
    assert SearchJobService(repository, resolver).policy(count)["vision_limit"] <= 8


async def test_restart_reuses_finished_models_and_keeps_inflight_reservation():
    """Аварийный запрос повторяется ограниченно; подтверждённый checkpoint не теряется."""
    repository, interrupted = FakeRepository(), AttemptResolver("MODEL-1")
    first = SearchJobService(repository, interrupted, max_vision_calls=0)
    job_id = uuid4()
    identities = [EquipmentIdentity("IEK", f"MODEL-{index}") for index in range(3)]
    await first.start(job_id, identities, False)
    await asyncio.wait_for(interrupted.stopped.wait(), 2)
    await first.shutdown()
    state = repository.jobs[job_id]
    assert state["status"] == "running"
    assert state["checkpoint"]["inflight"] == {"index": 1}
    assert state["checkpoint"]["queries_used"] == 2
    assert state["results"][0]["metrics"]["queries"] == 1
    state["status"] = "queued"  # В отдельном DB-тесте это делает recover_interrupted.
    resolver = AttemptResolver()
    second = SearchJobService(repository, resolver, max_vision_calls=0)
    await second._run(job_id)
    assert (0, "MODEL-0") not in resolver.calls
    assert resolver.calls[0] == (0, "MODEL-1")
    completed = await repository.get(job_id)
    assert completed["checkpoint"]["queries_used"] <= completed["policy"]["query_limit"]
    assert completed["results"][1]["metrics"]["interrupted_attempts"] == 1


async def test_resume_requires_revision_and_is_bounded():
    """Старая версия и четвёртое продолжение отклоняются, лимиты не бесконечны."""
    repository, resolver = FakeRepository(), AttemptResolver()
    service = SearchJobService(repository, resolver, max_vision_calls=0)
    job_id = uuid4()
    await service.start(job_id, [EquipmentIdentity("IEK", "MODEL-0")], False)
    await asyncio.gather(*service._tasks.values())
    for index in range(3):
        state = await repository.get(job_id)
        with pytest.raises(ValueError):
            await service.resume(job_id, state["revision"] - 1)
        await service.resume(job_id, state["revision"])
        await asyncio.gather(*service._tasks.values())
        assert (await repository.get(job_id))["resume_count"] == index + 1
    with pytest.raises(ValueError):
        await service.resume(job_id, (await repository.get(job_id))["revision"])


async def test_confirmed_model_is_not_reprocessed_on_resume():
    """Продолжение сохраняет документ и факты уже готовой модели."""
    repository, resolver = FakeRepository(), AttemptResolver()
    service = SearchJobService(repository, resolver, max_vision_calls=0)
    job_id = uuid4()
    identities = [
        EquipmentIdentity("IEK", "MODEL-0"),
        EquipmentIdentity("IEK", "MODEL-1"),
    ]
    await repository.create_or_get(job_id, [asdict(item) for item in identities], False)
    await repository.configure(job_id, service.policy(2))
    repository.jobs[job_id].update(
        status="incomplete",
        results=[
            {
                "identity": asdict(identities[0]),
                "status": "found",
                "document": {"source_id": "EQ-existing"},
                "facts": [{"value_raw": "12"}],
                "metrics": {},
                "attempts": [],
            },
            {
                "identity": asdict(identities[1]),
                "status": "incomplete",
                "metrics": {},
                "attempts": [],
            },
        ],
    )
    await service.resume(job_id, repository.jobs[job_id]["revision"])
    await asyncio.gather(*service._tasks.values())
    assert all(model != "MODEL-0" for _, model in resolver.calls)
    assert repository.jobs[job_id]["results"][0]["facts"] == [{"value_raw": "12"}]
