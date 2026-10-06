# services/knowledge-service/tests/functional/test_technical_assignment_retention.py

"""Защита HTTP-очистки ТЗ, сохранение требований владельца и удаление гостевых копий."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pdrd_knowledge_service.application.use_cases.technical_assignment_retention import (
    CleanupTechnicalAssignment,
)
from pdrd_knowledge_service.application.use_cases.technical_assignments import (
    GetTechnicalAssignmentContent,
    TechnicalAssignmentSourceExpiredError,
)
from pdrd_knowledge_service.core.container import ApplicationContainer
from pdrd_knowledge_service.core.settings import Settings
from pdrd_knowledge_service.domain.technical_assignment import (
    TechnicalAssignment,
    TechnicalAssignmentIndexStatus,
)
from pdrd_knowledge_service.transport.http.routers.technical_assignments import router

NOW = datetime(2026, 10, 6, tzinfo=UTC)


def assignment():
    """Создаёт индексированное ТЗ без рабочей БД."""
    return TechnicalAssignment(
        uuid4(),
        uuid4(),
        uuid4(),
        "ТЗ.pdf",
        "application/pdf",
        100,
        "a" * 64,
        TechnicalAssignmentIndexStatus.READY,
        None,
        NOW,
        NOW,
        NOW,
    )


class Work:
    """Фиксирует обращения к репозиторию и транзакции."""

    def __init__(self, value):
        """Создаёт управляемые порты."""
        self.assignments = SimpleNamespace(
            get_for_update=AsyncMock(return_value=value),
            update=AsyncMock(),
            delete=AsyncMock(),
        )
        self.commit = AsyncMock()

    async def __aenter__(self):
        """Возвращает тестовую транзакцию."""
        return self

    async def __aexit__(self, *args):
        """Закрывает транзакцию."""


def browser(cleanup, key="k" * 32):
    """Создаёт настоящий маршрут с тестовым сценарием и закрытым ключом."""
    app = FastAPI()
    settings = Settings(
        _env_file=None, technical_assignment={"retention_internal_key": key}
    )
    app.state.container = ApplicationContainer(
        settings=settings,
        search_normative=None,
        search_user_packages=None,
        search_experience=None,
        check_readiness=None,
        cleanup_technical_assignment=cleanup,
    )
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("key", [None, "wrong", ""])
def test_http_no_key_no_mutation(key):
    """Неверный ключ отвергается до изменения файлов."""
    use_case = SimpleNamespace(execute=AsyncMock())
    response = browser(use_case).delete(
        f"/internal/v1/technical-assignments/{uuid4()}/retention",
        params={"analysis_document_id": str(uuid4()), "sha256": "a" * 64},
        headers={"X-PDRD-Retention-Key": key} if key is not None else {},
    )
    assert response.status_code == 403
    use_case.execute.assert_not_awaited()


def test_http_authorized_contract():
    """UUID, хэш и явный режим передаются сценарию, путь от клиента не принимается."""
    use_case = SimpleNamespace(execute=AsyncMock())
    identifier, document = uuid4(), uuid4()
    response = browser(use_case).delete(
        f"/internal/v1/technical-assignments/{identifier}/retention",
        params={
            "analysis_document_id": str(document),
            "sha256": "a" * 64,
            "purge_metadata": "true",
        },
        headers={"X-PDRD-Retention-Key": "k" * 32},
    )
    assert response.status_code == 204
    use_case.execute.assert_awaited_once_with(
        technical_assignment_id=identifier,
        analysis_document_id=document,
        sha256="a" * 64,
        purge_metadata=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("purge", [False, True])
async def test_cleanup_preserves_owned_requirements_or_purges_guest(purge):
    """У владельца остаются требования; гостевые точки удаляются только по UUID ТЗ."""
    value = assignment()
    work = Work(value)
    storage, vectors = (
        SimpleNamespace(delete=AsyncMock()),
        SimpleNamespace(delete_by_filter=AsyncMock()),
    )
    cleaner = CleanupTechnicalAssignment(lambda: work, storage, vectors, "technical")
    await cleaner.execute(
        technical_assignment_id=value.technical_assignment_id,
        analysis_document_id=value.analysis_document_id,
        sha256=value.sha256,
        purge_metadata=purge,
    )
    storage.delete.assert_awaited_once_with(
        storage_key=f"{value.analysis_document_id}/{value.technical_assignment_id}.pdf"
    )
    if purge:
        vectors.delete_by_filter.assert_awaited_once_with(
            collection="technical",
            key="technical_assignment_id",
            value=str(value.technical_assignment_id),
        )
        work.assignments.delete.assert_awaited_once_with(value.technical_assignment_id)
        work.assignments.update.assert_not_awaited()
    else:
        vectors.delete_by_filter.assert_not_awaited()
        work.assignments.delete.assert_not_awaited()
        assert work.assignments.update.await_args.args[0].source_removed_at is not None
    work.commit.assert_awaited_once()


@pytest.mark.parametrize("alter", ["document", "hash", "queued", "indexing"])
def test_http_identity_or_active_index_conflict(alter):
    """Чужой документ и незавершённая индексация не удаляются."""
    value = assignment()
    if alter in ("queued", "indexing"):
        value = replace(
            value, index_status=TechnicalAssignmentIndexStatus(alter), indexed_at=None
        )
    work = Work(value)
    storage = SimpleNamespace(delete=AsyncMock())
    cleaner = CleanupTechnicalAssignment(
        lambda: work,
        storage,
        SimpleNamespace(delete_by_filter=AsyncMock()),
        "technical",
    )
    response = browser(cleaner).delete(
        f"/internal/v1/technical-assignments/{value.technical_assignment_id}/retention",
        params={
            "analysis_document_id": str(
                uuid4() if alter == "document" else value.analysis_document_id
            ),
            "sha256": "b" * 64 if alter == "hash" else value.sha256,
        },
        headers={"X-PDRD-Retention-Key": "k" * 32},
    )
    assert response.status_code == 409
    storage.delete.assert_not_awaited()
    work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_source_is_not_read_or_converted():
    """Истёкший исходник отклоняется до чтения и конвертации."""
    value = replace(assignment(), source_removed_at=NOW)
    storage, converter = (
        SimpleNamespace(read=AsyncMock()),
        SimpleNamespace(convert_to_pdf=AsyncMock()),
    )
    content = GetTechnicalAssignmentContent(
        SimpleNamespace(execute=AsyncMock(return_value=value)), storage, converter
    )
    with pytest.raises(TechnicalAssignmentSourceExpiredError):
        await content.execute(technical_assignment_id=value.technical_assignment_id)
    storage.read.assert_not_awaited()
    converter.convert_to_pdf.assert_not_awaited()
