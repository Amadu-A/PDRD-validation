# services/knowledge-service/tests/unit/test_document_context_http.py

"""Контракт отключения и защиты временного D-индекса на HTTP-границе."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pdrd_knowledge_service.transport.http.routers.document_context import (
    BuildDocumentContextRequest,
    SearchDocumentContextRequest,
    StaleDocumentContextsRequest,
    build_document_context,
    delete_document_context,
    search_document_context,
    stale_document_contexts,
)
from pydantic import SecretStr


class ForbiddenUseCase:
    """Обнаруживает случайный вызов индексации или поиска в быстром режиме."""

    async def execute(self, **kwargs):
        """При выключенном D сценарий не должен вызываться."""
        raise AssertionError("D отключён")


async def test_disabled_d_skips_configured_build_and_search():
    """Выбор пользователя блокирует затратные сценарии даже при включённом сервисе."""
    identity = uuid4()
    container = SimpleNamespace(
        build_document_context=ForbiddenUseCase(),
        search_document_context=ForbiddenUseCase(),
    )
    built = await build_document_context(
        BuildDocumentContextRequest(
            document_id=identity,
            enabled=False,
            source_sha256="a" * 64,
            pages=[{"page_number": 7, "text": "Текст"}],
        ),
        container,
    )
    found = await search_document_context(
        SearchDocumentContextRequest(context_id=identity, query="Б-012", enabled=False),
        container,
    )
    assert built.context_id == identity and built.chunks_count == 0
    assert found.sources == []


async def test_cleanup_routes_require_key_and_relay_bounded_cursor():
    """Очистка защищена служебным ключом, а поиск старых индексов ничего не удаляет."""
    identity = uuid4()
    calls = []

    class Index:
        """Фиксирует адресуемое задание и параметры ограниченного просмотра."""

        async def cleanup(self, *, context_id):
            """Записывает удаление конкретного задания."""
            calls.append(context_id)

        async def stale(self, **kwargs):
            """Передаёт возраст и курсор без чтения содержимого документа."""
            assert kwargs["limit"] == 2 and kwargs["cursor"] == "previous"
            return (identity,), "next"

    container = SimpleNamespace(
        document_context_index=Index(),
        settings=SimpleNamespace(
            technical_assignment=SimpleNamespace(
                retention_internal_key=SecretStr("k" * 32)
            ),
        ),
    )
    for key in (None, "wrong"):
        with pytest.raises(HTTPException) as error:
            await delete_document_context(identity, container, key)
        assert error.value.status_code == 403
    assert calls == []
    response = await stale_document_contexts(
        StaleDocumentContextsRequest(
            before=datetime.now(UTC), limit=2, cursor="previous"
        ),
        container,
        "k" * 32,
    )
    assert (
        response == {"document_ids": [str(identity)], "cursor": "next"} and calls == []
    )
    assert (
        await delete_document_context(identity, container, "k" * 32)
    ).status_code == 204
    assert calls == [identity]
