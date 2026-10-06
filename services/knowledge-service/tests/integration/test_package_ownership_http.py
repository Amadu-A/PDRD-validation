# services/knowledge-service/tests/integration/test_package_ownership_http.py

"""Проверяет внутреннюю HTTP-проверку владельца, включая обход через общий нормативный URL."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pdrd_knowledge_service.domain.normative_catalog import (
    CatalogArea,
    IndexingStatus,
    NormativeDocument,
)
from pdrd_knowledge_service.transport.http.dependencies import get_container
from pdrd_knowledge_service.transport.http.routers.normative_documents import router


def test_private_document_urls_require_exact_owner_before_content_or_mutation() -> None:
    """Личный PDF и историческая запись без владельца не выдаются общим normative API."""
    owner = uuid4()
    document_id = uuid4()
    now = datetime.now(UTC)
    document = NormativeDocument(
        document_id=document_id,
        section_id=uuid4(),
        category_id=None,
        original_name="personal.pdf",
        storage_key="private.pdf",
        mime_type="application/pdf",
        size_bytes=12,
        sha256="a" * 64,
        index_status=IndexingStatus.READY,
        index_error=None,
        indexed_at=now,
        created_at=now,
        updated_at=now,
        area=CatalogArea.USER_PACKAGE,
        owner_user_id=owner,
    )
    calls = []

    class Metadata:
        """Имитирует чтение БД, сохраняя настоящую доменную модель с владельцем."""

        async def execute(self, **options: object) -> NormativeDocument:
            """Возвращает текущую запись каталога."""
            return document

    class ProtectedOperation:
        """Сигнализирует, что операция с файлом началась после проверки UUID."""

        async def execute(self, **options: object) -> object:
            """Фиксирует успешный доступ к физическому PDF."""
            calls.append(options)
            return SimpleNamespace(content=b"%PDF-owned", mime_type="application/pdf")

    app = FastAPI()
    app.state.container = SimpleNamespace(
        normative_documents=SimpleNamespace(
            get_document=Metadata(),
            get_document_content=ProtectedOperation(),
            delete_document=ProtectedOperation(),
            move_document=ProtectedOperation(),
        ),
        queue_normative_document=ProtectedOperation(),
    )
    app.dependency_overrides[get_container] = lambda: app.state.container
    app.include_router(router)
    with TestClient(app) as client:
        base = f"/internal/v1/normative/documents/{document_id}"
        for headers in ({}, {"X-PDRD-Package-Owner": str(uuid4())}):
            assert client.get(base, headers=headers).status_code == 404
            assert client.get(base + "/content", headers=headers).status_code == 404
            assert client.delete(base, headers=headers).status_code == 404
            assert client.post(base + "/index", headers=headers).status_code == 404
            assert (
                client.patch(
                    base, json={"category_id": None}, headers=headers
                ).status_code
                == 404
            )
        assert calls == []
        assert (
            client.get(base, headers={"X-PDRD-Package-Owner": str(owner)}).status_code
            == 200
        )
        response = client.get(
            base + "/content", headers={"X-PDRD-Package-Owner": str(owner)}
        )
        assert response.content == b"%PDF-owned"
        assert len(calls) == 1
        document = replace(document, owner_user_id=None)
        assert (
            client.get(base, headers={"X-PDRD-Package-Owner": str(owner)}).status_code
            == 404
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("document_ids", [None, []])
async def test_section_alone_never_searches_all_personal_documents(
    document_ids: list | None,
) -> None:
    """Раздел без выбранных UUID не вызывает поиск по личным пакетам."""
    from pdrd_knowledge_service.application.use_cases.user_packages import (
        SearchUserPackages,
    )

    class Search:
        """Запрещает обращение к векторному поиску при пустом выборе."""

        embedding_model = "test"

        async def execute(self, *args: object, **kwargs: object) -> None:
            """Падает при запросе к невыбранным документам."""
            raise AssertionError("Не должно быть широкого поиска")

    result = await SearchUserPackages(Search()).execute(
        ["Проверка чертежа"],
        section_id=uuid4(),
        document_ids=document_ids,
    )
    assert result.sources == ()
