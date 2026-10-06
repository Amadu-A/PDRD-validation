# services/knowledge-service/tests/integration/test_section_deletion_http.py

"""Проверки HTTP повторного удаления раздела после внешнего сбоя."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pdrd_knowledge_service.application.ports.document_storage import (
    NormativeDocumentStorageError,
)
from pdrd_knowledge_service.application.ports.vector_store import VectorStoreError
from pdrd_knowledge_service.transport.http.dependencies import get_container
from pdrd_knowledge_service.transport.http.routers.normative_sections import router


@pytest.mark.parametrize("failure", [NormativeDocumentStorageError, VectorStoreError])
def test_failed_cleanup_returns_retryable_503_then_success(failure) -> None:
    """HTTP 503 не заявляет успех; повтор DELETE возвращает тот же UUID и 200."""
    section_id = uuid4()

    class Delete:
        """Моделирует частичную недоступность внешнего хранилища."""

        failed = True

        async def execute(self, *, section_id):
            """Вызывает безопасный повтор операции после доступности IO."""
            if self.failed:
                raise failure("offline")
            return section_id

    operation = Delete()
    container = SimpleNamespace(
        normative_sections=SimpleNamespace(delete_section=operation)
    )
    app = FastAPI()
    app.dependency_overrides[get_container] = lambda: container
    app.include_router(router)
    with TestClient(app) as client:
        url = f"/internal/v1/normative/sections/{section_id}"
        response = client.delete(url)
        assert response.status_code == 503
        assert "повторите удаление" in response.json()["detail"]
        operation.failed = False
        for _ in range(2):
            response = client.delete(url)
            assert response.status_code == 200
            assert response.json() == {"section_id": str(section_id), "deleted": True}
