# services/api-gateway/tests/unit/test_document_context_lifecycle.py

"""Границы очистки D в worker и страховочном сборщике Gateway."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from pdrd_api_gateway.application.ports.orchestration import (
    AnalysisOrchestrationError,
    AnalysisOrchestrationTransientError,
)
from pdrd_api_gateway.application.use_cases.cleanup_document_contexts import (
    CleanupDocumentContexts,
)
from pdrd_api_gateway.application.use_cases.execute_analysis_job import (
    AnalysisExecutionError,
    AnalysisJobCancelledError,
    AnalysisTransientExecutionError,
)
from pdrd_api_gateway.domain.analysis_job import AnalysisJob, AnalysisJobStatus

from .test_execute_analysis_job import (
    FakeArtifactStore,
    FakeOrchestrator,
    FakeUnitOfWorkFactory,
    build_submission,
    build_use_case,
)


class Lifecycle:
    """Фиксирует порядок удаления относительно терминального состояния."""

    def __init__(self, state, *, fails=False):
        """Сохраняет состояние и сценарий отказа внешнего сервиса."""
        self.state, self.calls, self.fails = state, [], fails
        self.candidates = ()

    async def cleanup(self, *, context_id):
        """Удаляет индекс либо сообщает временный сбой."""
        self.calls.append(
            (context_id, tuple(job.status for job in self.state.values()))
        )
        if self.fails:
            raise RuntimeError("Knowledge недоступен")

    async def stale(self, *, before, limit, cursor):
        """Возвращает тестовый ограниченный набор кандидатов."""
        return self.candidates[:limit], "next"


@pytest.mark.parametrize(
    "case", ["completed", "failed", "cancelled", "retry", "exception", "redelivery"]
)
async def test_worker_cleans_d_on_every_outcome(case):
    """Очистка работает также при отмене, повторной доставке и разрешённом retry."""
    request = build_submission()
    job = AnalysisJob.create(document_id=request.submission.document_id)
    job.mark_queued()
    if case == "cancelled":
        job.mark_cancelled()
    if case == "redelivery":
        job.mark_processing()
        job.mark_completed()
    state = {job.id: job}
    lifecycle = Lifecycle(state)
    error = (
        AnalysisOrchestrationTransientError("Повтор")
        if case == "retry"
        else (
            AnalysisOrchestrationError("Сбой")
            if case == "failed"
            else RuntimeError("Неожиданно")
            if case == "exception"
            else None
        )
    )
    store = FakeArtifactStore(
        request=request,
        result={"status": "completed"} if case == "redelivery" else None,
    )
    use_case = replace(
        build_use_case(
            state=state,
            artifact_store=store,
            orchestrator=FakeOrchestrator(error=error),
        ),
        document_context_lifecycle=lifecycle,
    )
    if case in ("completed", "redelivery"):
        await use_case.execute(job_id=job.id, allow_retry=True, redelivered=False)
    else:
        expected = (
            AnalysisJobCancelledError
            if case == "cancelled"
            else AnalysisTransientExecutionError
            if case == "retry"
            else AnalysisExecutionError
        )
        with pytest.raises(expected):
            await use_case.execute(job_id=job.id, allow_retry=True, redelivered=False)
    assert lifecycle.calls[0][0] == job.document_id
    assert len(lifecycle.calls) == 1
    assert lifecycle.calls[0][1] == (job.status,)


async def test_cleanup_failure_does_not_hide_saved_result():
    """Временный отказ удаления не переводит успешное задание в failed."""
    request = build_submission()
    job = AnalysisJob.create(document_id=request.submission.document_id)
    job.mark_queued()
    state = {job.id: job}
    use_case = replace(
        build_use_case(
            state=state,
            artifact_store=FakeArtifactStore(request=request),
            orchestrator=FakeOrchestrator(),
        ),
        document_context_lifecycle=Lifecycle(state, fails=True),
    )
    assert (
        await use_case.execute(job_id=job.id, allow_retry=False, redelivered=False)
    )["status"] == "completed"
    assert job.status is AnalysisJobStatus.COMPLETED


async def test_task_cancellation_also_cleans_d():
    """Отмена самой coroutine не обходит finally worker."""
    request = build_submission()
    job = AnalysisJob.create(document_id=request.submission.document_id)
    state = {job.id: job}
    lifecycle = Lifecycle(state)

    class CancelledOrchestrator:
        """Прерывает выполняемый запрос внешнего оркестратора."""

        async def execute(self, **kwargs):
            """Имитирует остановку процесса исполнения."""
            raise asyncio.CancelledError

    use_case = replace(
        build_use_case(
            state=state,
            artifact_store=FakeArtifactStore(request=request),
            orchestrator=CancelledOrchestrator(),
        ),
        document_context_lifecycle=lifecycle,
    )
    with pytest.raises(asyncio.CancelledError):
        await use_case.execute(job_id=job.id, allow_retry=False, redelivered=False)
    assert len(lifecycle.calls) == 1


async def test_sweeper_skips_active_job_and_deletes_terminal_and_orphan():
    """Одного возраста коллекции недостаточно для удаления активного задания."""
    active, terminal = (AnalysisJob.create(document_id=uuid4()) for _ in range(2))
    terminal.mark_failed(error_code="test", error_message="Сбой")
    state = {active.id: active, terminal.id: terminal}
    lifecycle = Lifecycle(state)
    orphan = uuid4()
    lifecycle.candidates = (active.document_id, terminal.document_id, orphan)

    class Factory(FakeUnitOfWorkFactory):
        """Добавляет поиск по document_id к тестовой транзакции."""

        def __call__(self):
            """Возвращает проверяемый серверный статус по UUID."""
            uow = super().__call__()

            async def by_document(document_id):
                """Имитирует row lock при страховочной очистке."""
                return next(
                    (job for job in state.values() if job.document_id == document_id),
                    None,
                )

            uow.analysis_jobs.get_by_document_id_for_update = by_document
            return uow

    cursor = await CleanupDocumentContexts(Factory(state), lifecycle, 3600).execute(
        limit=3
    )
    assert cursor == "next"
    assert [value[0] for value in lifecycle.calls] == [terminal.document_id, orphan]
