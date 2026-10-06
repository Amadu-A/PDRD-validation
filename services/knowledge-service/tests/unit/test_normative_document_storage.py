# services/knowledge-service/tests/unit/test_normative_document_storage.py

"""Проверки физического хранения нормативных документов."""

import asyncio
from pathlib import Path
from uuid import uuid4

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
    """Файловый адаптер сохраняет, читает и удаляет байты."""
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
    """Ключ хранилища не может выйти за пределы заданного корня."""
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


@pytest.mark.asyncio
async def test_delete_section_cleans_originals_previews_and_orphans_only(
    tmp_path: Path,
) -> None:
    """Удаление UUID-папки повторяемо и оставляет другой раздел нетронутым."""
    storage = LocalFilesystemNormativeDocumentStorage(root_path=tmp_path)
    section, other = uuid4(), uuid4()
    for key in (
        f"{section}/original.docx",
        f"{section}/original.preview.pdf",
        f"{section}/orphan.pdf",
        f"{section}/nested/temp.tmp",
        f"{other}/keep.pdf",
    ):
        await storage.save(storage_key=key, content=b"test")
    await storage.delete_section(section_id=section)
    await storage.delete_section(section_id=section)
    assert not (tmp_path / str(section)).exists()
    assert await storage.read(storage_key=f"{other}/keep.pdf") == b"test"


@pytest.mark.asyncio
async def test_delete_section_rejects_redirected_path(
    tmp_path: Path, monkeypatch
) -> None:
    """Резолвинг, указывающий на другую папку, запрещается до физического удаления."""
    section, other = uuid4(), uuid4()
    storage = LocalFilesystemNormativeDocumentStorage(root_path=tmp_path)
    await storage.save(storage_key=f"{other}/keep.pdf", content=b"test")
    original = Path.resolve
    root = await asyncio.to_thread(tmp_path.resolve)
    expected = root / str(section)
    redirected = root / str(other)

    def resolve(path, *args, **kwargs):
        """Имитирует перенаправление пути без Windows privileges для symlink."""
        return redirected if path == expected else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(NormativeDocumentStorageError, match="символической ссылкой"):
        await storage.delete_section(section_id=section)
    assert (redirected / "keep.pdf").read_bytes() == b"test"
