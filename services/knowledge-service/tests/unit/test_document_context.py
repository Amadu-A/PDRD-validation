# services/knowledge-service/tests/unit/test_document_context.py

"""Контракт выключенного D-контекста в существующем PDF workflow."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pdrd_knowledge_service.transport.http.routers.document_context import (
    BuildDocumentContextRequest,
    DocumentContextPagePayload,
    SearchDocumentContextRequest,
    build_document_context,
    search_document_context,
)


@pytest.mark.asyncio
async def test_disabled_document_context_keeps_workflow_running() -> None:
    """Выключенный D возвращает пустые результаты без ошибок HTTP."""
    document_id = uuid4()
    container = SimpleNamespace(
        build_document_context=None, search_document_context=None
    )
    built = await build_document_context(
        BuildDocumentContextRequest(
            document_id=document_id,
            source_sha256="a" * 64,
            pages=[DocumentContextPagePayload(page_number=1, text="План")],
        ),
        container,
    )
    searched = await search_document_context(
        SearchDocumentContextRequest(context_id=document_id, query="помещение"),
        container,
    )
    assert built.context_id == document_id
    assert built.chunks_count == 0
    assert searched.sources == []
