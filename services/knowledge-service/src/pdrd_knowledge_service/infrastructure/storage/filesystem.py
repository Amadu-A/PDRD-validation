# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/storage/filesystem.py

"""Filesystem adapter управляемых нормативных документов."""

import asyncio
import os
import shutil
from contextlib import suppress
from pathlib import (
    Path,
    PurePosixPath,
)
from uuid import UUID, uuid4

from pdrd_knowledge_service.application.ports.document_storage import (
    NormativeDocumentStorageError,
    NormativeDocumentStorageNotFoundError,
)


class LocalFilesystemNormativeDocumentStorage:
    """Хранит нормативные документы в persistent filesystem."""

    def __init__(
        self,
        *,
        root_path: Path,
    ) -> None:
        """Сохраняет root физического storage."""
        self._root_path = root_path

    async def save(
        self,
        *,
        storage_key: str,
        content: bytes,
    ) -> None:
        """Атомарно сохраняет документ вне цикла событий asyncio."""
        await asyncio.to_thread(
            self._save_sync,
            storage_key,
            content,
        )

    async def read(
        self,
        *,
        storage_key: str,
    ) -> bytes:
        """Читает документ вне цикла событий asyncio."""
        return await asyncio.to_thread(
            self._read_sync,
            storage_key,
        )

    async def delete(
        self,
        *,
        storage_key: str,
    ) -> None:
        """Идемпотентно удаляет документ."""
        await asyncio.to_thread(
            self._delete_sync,
            storage_key,
        )

    async def delete_section(self, *, section_id: UUID) -> None:
        """Идемпотентно очищает проверенную папку UUID, включая потерянные previews."""
        await asyncio.to_thread(self._delete_section_sync, section_id)

    def _delete_section_sync(self, section_id: UUID) -> None:
        """Проверяет абсолютную границу удаления и запрещает переадресующий symlink."""
        root = self._root_path.resolve()
        expected = root / str(section_id)
        if expected.is_symlink() or expected.resolve() != expected:
            raise NormativeDocumentStorageError(
                "Папка раздела не должна быть символической ссылкой."
            )
        try:
            shutil.rmtree(expected)
        except FileNotFoundError:
            return
        except OSError as error:
            raise NormativeDocumentStorageError(
                "Не удалось очистить папку раздела."
            ) from error

    def _save_sync(
        self,
        storage_key: str,
        content: bytes,
    ) -> None:
        """Сохраняет байты через временный файл с атомарной заменой."""
        path = self._resolve_path(
            storage_key,
        )

        temporary_path = path.with_name(
            f".{path.name}.{uuid4().hex}.tmp",
        )

        try:
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            if path.exists():
                raise NormativeDocumentStorageError(
                    f"Storage key уже существует: {storage_key}.",
                )

            with temporary_path.open(
                "xb",
            ) as stream:
                stream.write(
                    content,
                )

                stream.flush()

                os.fsync(
                    stream.fileno(),
                )

            temporary_path.replace(
                path,
            )

        except NormativeDocumentStorageError:
            with suppress(
                OSError,
            ):
                temporary_path.unlink()

            raise

        except OSError as error:
            with suppress(
                OSError,
            ):
                temporary_path.unlink()

            raise NormativeDocumentStorageError(
                "Не удалось сохранить нормативный документ.",
            ) from error

    def _read_sync(
        self,
        storage_key: str,
    ) -> bytes:
        """Читает bytes физического документа."""
        path = self._resolve_path(
            storage_key,
        )

        if not path.is_file():
            raise NormativeDocumentStorageNotFoundError(
                f"Файл storage_key={storage_key} не найден.",
            )

        try:
            return path.read_bytes()

        except OSError as error:
            raise NormativeDocumentStorageError(
                "Не удалось прочитать нормативный документ.",
            ) from error

    def _delete_sync(
        self,
        storage_key: str,
    ) -> None:
        """Идемпотентно удаляет physical file."""
        path = self._resolve_path(
            storage_key,
        )

        try:
            path.unlink(
                missing_ok=True,
            )

            if path.parent != self._root_path:
                with suppress(
                    OSError,
                ):
                    path.parent.rmdir()

        except OSError as error:
            raise NormativeDocumentStorageError(
                "Не удалось удалить нормативный документ.",
            ) from error

    def _resolve_path(
        self,
        storage_key: str,
    ) -> Path:
        """Преобразует безопасный внутренний ключ в путь файловой системы."""
        if not storage_key.strip():
            raise NormativeDocumentStorageError(
                "Storage key не может быть пустым.",
            )

        relative = PurePosixPath(
            storage_key,
        )

        if relative.is_absolute() or ".." in relative.parts:
            raise NormativeDocumentStorageError(
                "Недопустимый storage key нормативного документа.",
            )

        root = self._root_path.resolve()
        expected = root.joinpath(*relative.parts)
        if expected.resolve() != expected:
            raise NormativeDocumentStorageError(
                "Путь документа не должен проходить через символическую ссылку."
            )
        return expected
