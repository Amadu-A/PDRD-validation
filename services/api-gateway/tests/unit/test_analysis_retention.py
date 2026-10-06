# services/api-gateway/tests/unit/test_analysis_retention.py

"""Границы 30/7 дней, повтор очистки, сохранность результатов и частичные ошибки."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pdrd_api_gateway.application.use_cases.cleanup_analysis_retention import (
    CleanupAnalysisRetention,
)
from pdrd_api_gateway.application.use_cases.get_analysis_result import GetAnalysisResult
from pdrd_api_gateway.domain.analysis_job import AnalysisJob, AnalysisJobStatus
from pdrd_api_gateway.domain.analysis_retention import (
    AnalysisRetentionPolicy,
    RetentionAction,
)
from pdrd_api_gateway.domain.normative_snapshot import NormativeAnalysisSnapshot
from pdrd_api_gateway.domain.technical_assignment import TechnicalAssignmentSnapshot
from pdrd_api_gateway.infrastructure.knowledge.analysis_retention import (
    HttpTechnicalAssignmentRetention,
)
from pdrd_api_gateway.infrastructure.storage.analysis_retention import (
    LocalAnalysisRetentionArtifacts,
)
from pdrd_api_gateway.infrastructure.storage.filesystem import (
    LocalFilesystemAnalysisArtifactStore,
)

NOW = datetime(2026, 11, 6, tzinfo=UTC)


def job(*, owned=True, age=30, status=AnalysisJobStatus.COMPLETED):
    """Создаёт завершённое или активное задание заданного возраста."""
    return replace(
        AnalysisJob.create(
            document_id=uuid4(), owner_user_id=uuid4() if owned else None
        ),
        status=status,
        created_at=NOW - timedelta(days=age),
    )


class MemoryWork:
    """Тестовая транзакция откатывает изменения строки при исключении."""

    def __init__(self, rows):
        """Разделяет хранилище между отдельными транзакциями."""
        self.rows = rows
        self.analysis_jobs = self
        self.referenced = self.owned_reference = False

    async def __aenter__(self):
        """Сохраняет снимок для отката."""
        self.original = deepcopy(self.rows)
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        """Откатывает частично изменённые строки."""
        if exc_type:
            self.rows.clear()
            self.rows.update(self.original)

    async def commit(self):
        """Имитирует успешную фиксацию."""

    async def list_retention_candidates(self, **kwargs):
        """Намеренно возвращает и активные строки для проверки второго барьера."""
        return list(self.rows.values())[: kwargs["limit"]]

    async def get_for_update(self, identifier):
        """Читает актуальную строку, а не прежний снимок кандидата."""
        return self.rows.get(identifier)

    async def update(self, value):
        """Сохраняет отметку очистки исходников."""
        self.rows[value.id] = value

    async def delete(self, identifier):
        """Удаляет метаданные гостя."""
        del self.rows[identifier]

    async def technical_assignment_retention(self, **kwargs):
        """Имитирует ссылки на общее ТЗ в других заданиях."""
        return self.referenced, self.owned_reference


@pytest.mark.parametrize(
    "owned,days,expected",
    [
        (True, 29, RetentionAction.KEEP),
        (True, 30, RetentionAction.REMOVE_SOURCES),
        (False, 6, RetentionAction.KEEP),
        (False, 7, RetentionAction.REMOVE_GUEST),
    ],
)
def test_retention_exact_boundary(owned, days, expected):
    """Граница включительна, за микросекунду до неё исходники остаются."""
    value = job(owned=owned, age=days)
    policy = AnalysisRetentionPolicy()
    assert policy.action(value, now=NOW) is expected
    if expected != RetentionAction.KEEP:
        assert (
            policy.action(value, now=NOW - timedelta(microseconds=1))
            is RetentionAction.KEEP
        )


@pytest.mark.parametrize(
    "status",
    [AnalysisJobStatus.PENDING, AnalysisJobStatus.QUEUED, AnalysisJobStatus.PROCESSING],
)
@pytest.mark.parametrize("owned", [True, False])
def test_never_remove_active_analysis(status, owned):
    """Даже старое задание в очереди или в работе не очищается."""
    assert (
        AnalysisRetentionPolicy().action(
            job(age=90, status=status, owned=owned), now=NOW
        )
        is RetentionAction.KEEP
    )


@pytest.mark.asyncio
async def test_owned_sources_removed_result_history_review_experience_preserved(
    tmp_path,
):
    """Реальные файлы: JSON остаются, бинарные источники и производные копии исчезают."""
    value = job()
    folder = tmp_path / str(value.document_id)
    folder.mkdir()
    preserved = {
        "request.json": '{"pdf_file_name":"test.pdf"}',
        "result.json": '{"findings":[{"text":"Замечание"}]}',
        "history.json": '{"file_name":"test.pdf"}',
        "review.json": '{"revision":3}',
    }
    for name, content in preserved.items():
        (folder / name).write_text(content, encoding="utf-8")
    for name in ("pdf.bin", "cad.bin", "technical_assignment.bin", "annotated-v7.pdf"):
        (folder / name).write_bytes(b"source")
    (folder / "visualization").mkdir()
    (folder / "visualization" / "1.png").write_bytes(b"image")
    cache = tmp_path / "reviewed-pdf" / str(value.id)
    cache.mkdir(parents=True)
    (cache / "reviewed-current.cache").write_bytes(b"PDF")
    experience = tmp_path / "experience"
    experience.mkdir()
    (experience / "training.png").write_bytes(b"indefinite")
    rows = {value.id: value}
    work = MemoryWork(rows)
    cleaner = CleanupAnalysisRetention(
        lambda: work, LocalAnalysisRetentionArtifacts(root_path=tmp_path)
    )
    report = await cleaner.execute(now=NOW)
    assert report.sources_removed == 1 and report.guests_removed == report.failed == 0
    assert rows[value.id].source_artifacts_deleted_at == NOW
    assert {p.name for p in folder.iterdir()} == set(preserved)
    assert all(
        (folder / name).read_text("utf-8") == content
        for name, content in preserved.items()
    )
    assert (
        not cache.exists()
        and (experience / "training.png").read_bytes() == b"indefinite"
    )
    assert (await cleaner.execute(now=NOW)).sources_removed == 0
    store = LocalFilesystemAnalysisArtifactStore(root_path=tmp_path)
    result = await GetAnalysisResult(
        SimpleNamespace(execute=AsyncMock(return_value=value)), store
    ).execute(job_id=value.id)
    assert (
        result["findings"] == [{"text": "Замечание"}]
        and result["source_artifacts_expired"]
    )
    assert "source_artifacts_expired" not in (
        await store.load_result(document_id=value.document_id)
    )


@pytest.mark.asyncio
async def test_guest_link_separate_from_seven_day_cleanup(tmp_path):
    """Гостевой результат не удаляется через сутки; в семь дней удаляются и файлы, и строка."""
    value = job(owned=False, age=1)
    folder = tmp_path / str(value.document_id)
    folder.mkdir()
    (folder / "result.json").write_text("{}", encoding="utf-8")
    rows = {value.id: value}
    cleaner = CleanupAnalysisRetention(
        lambda: MemoryWork(rows), LocalAnalysisRetentionArtifacts(root_path=tmp_path)
    )
    assert (await cleaner.execute(now=NOW)).guests_removed == 0
    assert folder.exists() and value.id in rows
    report = await cleaner.execute(now=NOW + timedelta(days=6))
    assert report.guests_removed == 1 and not folder.exists() and not rows
    assert (await cleaner.execute(now=NOW + timedelta(days=6))).guests_removed == 0


@pytest.mark.asyncio
async def test_failure_keeps_metadata_and_does_not_stop_other_jobs():
    """Ошибка файловой системы не маскируется удалением строки; повтор завершает очистку."""
    first, second = job(owned=False, age=7), job()
    rows = {value.id: value for value in (first, second)}
    artifacts = SimpleNamespace(
        remove_guest=AsyncMock(side_effect=OSError("busy")), remove_sources=AsyncMock()
    )
    cleaner = CleanupAnalysisRetention(lambda: MemoryWork(rows), artifacts)
    report = await cleaner.execute(now=NOW)
    assert report.failed == 1 and report.sources_removed == 1 and first.id in rows
    artifacts.remove_guest.side_effect = None
    report = await cleaner.execute(now=NOW)
    assert (
        report.guests_removed == 1
        and report.sources_removed == 0
        and report.failed == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retained,owned,guest,purge",
    [
        (True, False, True, None),
        (False, True, True, False),
        (False, False, True, True),
        (False, False, False, False),
    ],
)
async def test_shared_assignment_protects_other_analysis(retained, owned, guest, purge):
    """Общее ТЗ нельзя удалить, пока оно нужно свежей проверке или истории владельца."""
    value = job(owned=not guest)
    section = uuid4()
    assignment = TechnicalAssignmentSnapshot.create(
        analysis_document_id=uuid4(),
        section_id=section,
        source_file="tz.pdf",
        content=b"%PDF-TZ",
    )
    value.normative_snapshot = NormativeAnalysisSnapshot.create(
        section_id=section,
        document_ids=(),
        system_prompt="Проверить",
        technical_assignment=assignment,
    )
    rows = {value.id: value}
    work = MemoryWork(rows)
    work.referenced, work.owned_reference = retained, owned
    external = SimpleNamespace(remove_source=AsyncMock())
    cleaner = CleanupAnalysisRetention(
        lambda: work,
        SimpleNamespace(remove_guest=AsyncMock(), remove_sources=AsyncMock()),
        technical_assignments=external,
    )
    assert (await cleaner.execute(now=NOW)).failed == 0
    if purge is None:
        external.remove_source.assert_not_awaited()
    else:
        external.remove_source.assert_awaited_once_with(
            assignment, purge_metadata=purge
        )


@pytest.mark.asyncio
async def test_http_assignment_retention_contract_and_idempotent_404():
    """Передаёт только серверный ключ и снимок, не пути; отказ сервиса не скрывается."""
    snapshot = TechnicalAssignmentSnapshot.create(
        analysis_document_id=uuid4(),
        section_id=uuid4(),
        source_file="tz.pdf",
        content=b"%PDF-TZ",
    )
    requests = []
    statuses = iter([204, 404, 503])

    def handle(request):
        """Проверяет контракт реального HTTP-адаптера."""
        requests.append(request)
        return httpx.Response(next(statuses))

    adapter = HttpTechnicalAssignmentRetention(
        base_url="http://knowledge",
        internal_key="k" * 32,
        transport=httpx.MockTransport(handle),
    )
    await adapter.remove_source(snapshot, purge_metadata=False)
    await adapter.remove_source(snapshot, purge_metadata=True)
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.remove_source(snapshot, purge_metadata=False)
    assert requests[0].headers["X-PDRD-Retention-Key"] == "k" * 32
    assert requests[0].url.params["analysis_document_id"] == str(
        snapshot.analysis_document_id
    )
    assert requests[0].url.params["purge_metadata"] == "false"
    assert requests[1].url.params["purge_metadata"] == "true"


@pytest.mark.asyncio
async def test_retention_refuses_external_symlink(tmp_path):
    """Внешняя ссылка не позволяет удалять чужой каталог."""
    root, outside = tmp_path / "analyses", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    identifier = uuid4()
    try:
        (root / str(identifier)).symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip(
            "ОС не разрешает создание символических ссылок без дополнительных прав"
        )
    (outside / "pdf.bin").write_bytes(b"safe")
    with pytest.raises(ValueError):
        await LocalAnalysisRetentionArtifacts(root_path=root).remove_guest(
            document_id=identifier, job_id=uuid4()
        )
    assert (outside / "pdf.bin").read_bytes() == b"safe"


@pytest.mark.asyncio
async def test_missing_directory_is_idempotent(tmp_path):
    """Повтор после частичного удаления не создаёт каталоги заново."""
    storage = LocalAnalysisRetentionArtifacts(root_path=tmp_path)
    await storage.remove_sources(document_id=uuid4(), job_id=uuid4())
    await storage.remove_guest(document_id=uuid4(), job_id=uuid4())
    assert not list(tmp_path.iterdir())
