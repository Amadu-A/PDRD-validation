# services/knowledge-service/tests/unit/test_normative_document_storage.py

"""Unit tests filesystem storage нормативных документов."""

from pathlib import Path

import pytest
from pdrd_knowledge_service.application.ports.document_storage import (
    NormativeDocumentStorageError,
    NormativeDocumentStorageNotFoundError,
)
from pdrd_knowledge_service.infrastructure.storage.filesystem import (
    LocalFilesystemNormativeDocumentStorage,
)


@pytest.mark.asyncio
async def test_filesystem_storage_save_read_delete(
    tmp_path: Path,
) -> None:
    """Filesystem adapter сохраняет, читает и удаляет bytes."""
    storage = LocalFilesystemNormativeDocumentStorage(
        root_path=tmp_path,
    )

    content = b"%PDF-1.7\ncontent\n%%EOF"

    await storage.save(
        storage_key="section/document.pdf",
        content=content,
    )

    assert (
        await storage.read(
            storage_key="section/document.pdf",
        )
        == content
    )

    await storage.delete(
        storage_key="section/document.pdf",
    )

    with pytest.raises(
        NormativeDocumentStorageNotFoundError,
    ):
        await storage.read(
            storage_key="section/document.pdf",
        )


@pytest.mark.asyncio
async def test_filesystem_storage_rejects_path_traversal(
    tmp_path: Path,
) -> None:
    """Storage key не может выйти за пределы configured root."""
    storage = LocalFilesystemNormativeDocumentStorage(
        root_path=tmp_path,
    )

    with pytest.raises(
        NormativeDocumentStorageError,
        match="storage key",
    ):
        await storage.save(
            storage_key="../escape.pdf",
            content=b"%PDF-1.7\n%%EOF",
        )

    assert not (tmp_path.parent / "escape.pdf").exists()


@pytest.mark.asyncio
async def test_filesystem_storage_does_not_overwrite_existing_file(
    tmp_path: Path,
) -> None:
    """Повторная запись того же storage key запрещена."""
    storage = LocalFilesystemNormativeDocumentStorage(
        root_path=tmp_path,
    )

    await storage.save(
        storage_key="section/document.pdf",
        content=b"first",
    )

    with pytest.raises(
        NormativeDocumentStorageError,
        match="уже существует",
    ):
        await storage.save(
            storage_key="section/document.pdf",
            content=b"second",
        )

    assert (
        await storage.read(
            storage_key="section/document.pdf",
        )
        == b"first"
    )
