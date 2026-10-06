# services/api-gateway/tests/unit/test_analysis_history.py

"""История собственного анализа, старые артефакты и отсутствие побочных эффектов."""

import asyncio
from dataclasses import replace
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.application.use_cases.list_analysis_history import (
    ListAnalysisHistory,
)
from pdrd_api_gateway.domain.analysis_job import AnalysisJob, AnalysisJobStatus
from pdrd_api_gateway.domain.analysis_submission import AnalysisSubmission
from pdrd_api_gateway.domain.normative_snapshot import NormativeAnalysisSnapshot
from pdrd_api_gateway.infrastructure.storage.filesystem import (
    LocalFilesystemAnalysisArtifactStore,
)


class Work:
    """Транзакция чтения без возможности опубликовать новое задание."""

    def __init__(self, rows):
        """Фиксирует вызов порта для проверки границы владельца."""
        self.analysis_jobs = SimpleNamespace(list_by_owner=AsyncMock(return_value=rows))

    async def __aenter__(self):
        """Открывает тестовую транзакцию."""
        return self

    async def __aexit__(self, *args):
        """Завершает чтение без записи."""


async def stored(store, owner, *, section=None):
    """Сохраняет настоящие артефакты завершённого PDF без сети и VLM."""
    submission = AnalysisSubmission.create(
        pdf_present=True,
        cad_present=False,
        pages=None,
        pdf_file_name="ВК.pdf",
        cad_file_name=None,
    )
    await store.save_request(
        submission=submission, pdf_content=b"source", cad_content=None
    )
    await store.save_result(
        document_id=submission.document_id,
        result={
            "source_mode": "pdf_only",
            "status": "completed",
            "analyzed_pages": 18,
            "findings_count": 7,
            "findings": [{"comment": "Чувствительный текст"}],
        },
    )
    return replace(
        AnalysisJob.create(
            document_id=submission.document_id,
            owner_user_id=owner,
            normative_snapshot=section,
        ),
        status=AnalysisJobStatus.COMPLETED,
    )


@pytest.mark.asyncio
async def test_owned_history_reads_existing_result_and_approved_review(tmp_path):
    """История не читает PDF, не открывает Review и не возвращает исходные замечания."""
    owner, section_id = uuid4(), uuid4()
    store = LocalFilesystemAnalysisArtifactStore(root_path=tmp_path)
    snapshot = NormativeAnalysisSnapshot.create(
        section_id=section_id, document_ids=(), system_prompt="secret prompt"
    )
    job = await stored(store, owner, section=snapshot)
    work = Work([job, job])
    reviews = SimpleNamespace(
        execute=AsyncMock(return_value={"revision": 4, "approved_revision": 4})
    )
    sections = SimpleNamespace(
        list_sections=AsyncMock(
            return_value=[SimpleNamespace(section_id=section_id, name="ОВ")]
        )
    )
    use_case = ListAnalysisHistory(lambda: work, store, sections, reviews)
    result = await use_case.execute(
        owner_user_id=owner, limit=1, can_download_reviewed_pdf=True
    )
    item = result["items"][0]
    assert result["has_more"] is True
    assert (
        item["file_name"],
        item["pages_count"],
        item["findings_count"],
        item["section_name"],
    ) == ("ВК.pdf", 18, 7, "ОВ")
    assert item["review_status"] == "approved" and item["review_revision"] == 4
    assert item["open_url"] == f"/?job_id={job.id}"
    assert item["pdf_url"] == f"/api/v1/analyses/{job.id}/reviewed-pdf"
    assert "secret prompt" not in str(result) and "Чувствительный текст" not in str(
        result
    )
    work.analysis_jobs.list_by_owner.assert_awaited_once_with(
        owner_user_id=owner, limit=2, offset=0
    )
    assert reviews.execute.await_args.kwargs["context"].operation == "read"
    assert reviews.execute.await_args.kwargs["context"].actor == f"user:{owner}"
    # При повторном чтении хватает компактной сводки, полный JSON и PDF не открываются.
    store._load_result_sync = lambda _: pytest.fail(
        "Повторное чтение полного результата"
    )
    await use_case.execute(owner_user_id=owner, limit=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [(404, "not_opened"), (503, "unavailable")])
async def test_review_outage_preserves_analysis_and_automatic_pdf(
    tmp_path, status, expected
):
    """Сбой Review не скрывает результат и не выдаёт право на PDF после Review."""
    owner = uuid4()
    store = LocalFilesystemAnalysisArtifactStore(root_path=tmp_path)
    job = await stored(store, owner)
    reviews = SimpleNamespace(
        execute=AsyncMock(side_effect=ReviewRequestError(status, "Нет ответа"))
    )
    history = await ListAnalysisHistory(
        lambda: Work([job]), store, reviews=reviews
    ).execute(owner_user_id=owner)
    assert history["items"][0]["review_status"] == expected
    assert history["items"][0]["pdf_kind"] == "annotated"


@pytest.mark.asyncio
async def test_wrong_owner_is_denied_before_reading_any_artifacts():
    """Даже ошибочный адаптер не приводит к чтению файлов другого пользователя."""
    artifacts = SimpleNamespace(load_summary=AsyncMock())
    foreign = AnalysisJob.create(document_id=uuid4(), owner_user_id=uuid4())
    with pytest.raises(ValueError, match="принадлежность"):
        await ListAnalysisHistory(lambda: Work([foreign]), artifacts).execute(
            owner_user_id=uuid4()
        )
    artifacts.load_summary.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_missing_artifacts_remains_visible_without_pdf(tmp_path):
    """Задание сохраняется в истории, даже если файлы были удалены вручную."""
    owner = uuid4()
    job = replace(
        AnalysisJob.create(document_id=uuid4(), owner_user_id=owner),
        status=AnalysisJobStatus.COMPLETED,
    )
    store = LocalFilesystemAnalysisArtifactStore(root_path=tmp_path)
    history = await ListAnalysisHistory(lambda: Work([job]), store).execute(
        owner_user_id=owner
    )
    assert history["items"][0]["job_id"] == str(job.id)
    assert history["items"][0]["pdf_url"] is None
    assert history["items"][0]["result_available"] is False


@pytest.mark.asyncio
async def test_parallel_first_read_of_legacy_summary_is_atomic(tmp_path):
    """Две вкладки впервые читают один старый результат без гонки общего временного файла."""
    store = LocalFilesystemAnalysisArtifactStore(root_path=tmp_path)
    job = await stored(store, uuid4())
    original = store._load_result_sync
    barrier = Barrier(2)

    def read(document_id):
        """Гарантирует, что оба потока дошли до записи сводки одновременно."""
        result = original(document_id)
        barrier.wait(timeout=5)
        return result

    store._load_result_sync = read
    first, second = await asyncio.gather(
        store.load_summary(document_id=job.document_id),
        store.load_summary(document_id=job.document_id),
    )
    assert first == second and first.file_name == "ВК.pdf"
    assert list(tmp_path.rglob("*.tmp")) == []
