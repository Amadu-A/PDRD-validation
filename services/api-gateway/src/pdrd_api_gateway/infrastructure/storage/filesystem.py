# services/api-gateway/src/pdrd_api_gateway/infrastructure/storage/filesystem.py

"""Filesystem adapter временного хранения артефактов анализа."""

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisArtifactsNotFoundError,
    AnalysisArtifactStorageError,
    AnalysisRequestArtifacts,
)
from pdrd_api_gateway.domain.analysis_submission import (
    AnalysisSourceMode,
    AnalysisSubmission,
)


class LocalFilesystemAnalysisArtifactStore:
    """Хранит пользовательские файлы по ключу document_id."""

    _MANIFEST_FILE = "request.json"
    _PDF_FILE = "pdf.bin"
    _CAD_FILE = "cad.bin"
    _RESULT_FILE = "result.json"

    def __init__(
        self,
        *,
        root_path: Path,
    ) -> None:
        """Сохраняет корневой каталог artifact storage."""
        self._root_path = root_path

    async def save_request(
        self,
        *,
        submission: AnalysisSubmission,
        pdf_content: bytes | None,
        cad_content: bytes | None,
    ) -> None:
        """Сохраняет заявку без блокировки asyncio event loop."""
        await asyncio.to_thread(
            self._save_request_sync,
            submission,
            pdf_content,
            cad_content,
        )

    async def load_request(
        self,
        *,
        document_id: UUID,
    ) -> AnalysisRequestArtifacts:
        """Загружает manifest и исходные файлы."""
        return await asyncio.to_thread(
            self._load_request_sync,
            document_id,
        )

    async def delete_request(
        self,
        *,
        document_id: UUID,
    ) -> None:
        """Удаляет каталог document_id."""
        await asyncio.to_thread(
            self._delete_request_sync,
            document_id,
        )

    async def save_result(
        self,
        *,
        document_id: UUID,
        result: dict[str, Any],
    ) -> None:
        """Атомарно сохраняет итоговый JSON."""
        await asyncio.to_thread(
            self._save_result_sync,
            document_id,
            result,
        )

    async def load_result(
        self,
        *,
        document_id: UUID,
    ) -> dict[str, Any] | None:
        """Возвращает сохранённый результат."""
        return await asyncio.to_thread(
            self._load_result_sync,
            document_id,
        )

    def _save_request_sync(
        self,
        submission: AnalysisSubmission,
        pdf_content: bytes | None,
        cad_content: bytes | None,
    ) -> None:
        directory = self._document_directory(
            submission.document_id,
        )

        try:
            self._root_path.mkdir(
                parents=True,
                exist_ok=True,
            )

            directory.mkdir(
                parents=False,
                exist_ok=False,
            )

            manifest = {
                "document_id": str(
                    submission.document_id,
                ),
                "source_mode": (submission.source_mode.value),
                "pages": submission.pages,
                "pdf_file_name": (submission.pdf_file_name),
                "cad_file_name": (submission.cad_file_name),
            }

            self._write_json_atomic(
                directory / self._MANIFEST_FILE,
                manifest,
            )

            if pdf_content is not None:
                (directory / self._PDF_FILE).write_bytes(
                    pdf_content,
                )

            if cad_content is not None:
                (directory / self._CAD_FILE).write_bytes(
                    cad_content,
                )

        except (
            OSError,
            TypeError,
            ValueError,
        ) as error:
            shutil.rmtree(
                directory,
                ignore_errors=True,
            )

            raise AnalysisArtifactStorageError(
                "Не удалось сохранить исходные файлы анализа.",
            ) from error

    def _load_request_sync(
        self,
        document_id: UUID,
    ) -> AnalysisRequestArtifacts:
        directory = self._document_directory(
            document_id,
        )

        manifest_path = directory / self._MANIFEST_FILE

        if not manifest_path.is_file():
            raise AnalysisArtifactsNotFoundError(
                f"Артефакты document_id={document_id} не найдены.",
            )

        try:
            manifest = json.loads(
                manifest_path.read_text(
                    encoding="utf-8",
                )
            )

            submission = AnalysisSubmission(
                document_id=UUID(
                    manifest["document_id"],
                ),
                source_mode=AnalysisSourceMode(
                    manifest["source_mode"],
                ),
                pages=manifest.get(
                    "pages",
                ),
                pdf_file_name=manifest.get(
                    "pdf_file_name",
                ),
                cad_file_name=manifest.get(
                    "cad_file_name",
                ),
            )

            pdf_path = directory / self._PDF_FILE

            cad_path = directory / self._CAD_FILE

            pdf_content = pdf_path.read_bytes() if pdf_path.is_file() else None

            cad_content = cad_path.read_bytes() if cad_path.is_file() else None

            return AnalysisRequestArtifacts(
                submission=submission,
                pdf_content=pdf_content,
                cad_content=cad_content,
            )

        except (
            KeyError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise AnalysisArtifactStorageError(
                "Не удалось прочитать сохранённые артефакты анализа.",
            ) from error

    def _delete_request_sync(
        self,
        document_id: UUID,
    ) -> None:
        directory = self._document_directory(
            document_id,
        )

        try:
            shutil.rmtree(
                directory,
                ignore_errors=True,
            )
        except OSError as error:
            raise AnalysisArtifactStorageError(
                "Не удалось удалить артефакты анализа.",
            ) from error

    def _save_result_sync(
        self,
        document_id: UUID,
        result: dict[str, Any],
    ) -> None:
        directory = self._document_directory(
            document_id,
        )

        if not directory.is_dir():
            raise AnalysisArtifactsNotFoundError(
                f"Артефакты document_id={document_id} не найдены.",
            )

        try:
            self._write_json_atomic(
                directory / self._RESULT_FILE,
                result,
            )
        except (
            OSError,
            TypeError,
            ValueError,
        ) as error:
            raise AnalysisArtifactStorageError(
                "Не удалось сохранить результат анализа.",
            ) from error

    def _load_result_sync(
        self,
        document_id: UUID,
    ) -> dict[str, Any] | None:
        path = (
            self._document_directory(
                document_id,
            )
            / self._RESULT_FILE
        )

        if not path.is_file():
            return None

        try:
            payload = json.loads(
                path.read_text(
                    encoding="utf-8",
                )
            )

            if not isinstance(
                payload,
                dict,
            ):
                raise ValueError(
                    "Analysis result must be JSON object.",
                )

            return payload

        except (
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise AnalysisArtifactStorageError(
                "Не удалось прочитать результат анализа.",
            ) from error

    def _document_directory(
        self,
        document_id: UUID,
    ) -> Path:
        return self._root_path / str(
            document_id,
        )

    @staticmethod
    def _write_json_atomic(
        path: Path,
        payload: dict[str, Any],
    ) -> None:
        temporary_path = path.with_suffix(
            f"{path.suffix}.tmp",
        )

        temporary_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(
                    ",",
                    ":",
                ),
            ),
            encoding="utf-8",
        )

        temporary_path.replace(
            path,
        )
