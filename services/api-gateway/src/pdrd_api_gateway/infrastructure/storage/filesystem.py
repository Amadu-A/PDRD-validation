# services/api-gateway/src/pdrd_api_gateway/infrastructure/storage/filesystem.py

"""Filesystem adapter временного хранения артефактов анализа."""

import asyncio
import base64
import binascii
import json
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
    AnalysisPagePreview,
    AnalysisTextWord,
)
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

    _TECHNICAL_ASSIGNMENT_FILE = "technical_assignment.bin"

    _RESULT_FILE = "result.json"

    _VISUALIZATION_DIRECTORY = "visualization"

    _VISUALIZATION_MANIFEST_FILE = "manifest.json"

    _VISUALIZATION_SCHEMA_VERSION = 1

    _PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

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

    async def save_technical_assignment(
        self,
        *,
        document_id: UUID,
        content: bytes,
    ) -> None:
        """Атомарно сохраняет исходные bytes ТЗ."""
        await asyncio.to_thread(
            self._save_technical_assignment_sync,
            document_id,
            content,
        )

    async def load_technical_assignment(
        self,
        *,
        document_id: UUID,
    ) -> bytes | None:
        """Возвращает сохранённое ТЗ."""
        return await asyncio.to_thread(
            self._load_technical_assignment_sync,
            document_id,
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

    async def save_visualization(
        self,
        *,
        document_id: UUID,
        pages: tuple[
            AnalysisPagePreview,
            ...,
        ],
    ) -> None:
        """Сохраняет reusable PDF PNG/text geometry отдельно от result JSON."""
        await asyncio.to_thread(
            self._save_visualization_sync,
            document_id,
            pages,
        )

    async def load_visualization(
        self,
        *,
        document_id: UUID,
    ) -> (
        tuple[
            AnalysisPagePreview,
            ...,
        ]
        | None
    ):
        """Возвращает reusable visualization artifact либо None для legacy job."""
        return await asyncio.to_thread(
            self._load_visualization_sync,
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
                "use_explanatory_note": (submission.use_explanatory_note),
                "note_start_page": (submission.note_start_page),
                "note_end_page": (submission.note_end_page),
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

    def _save_technical_assignment_sync(
        self,
        document_id: UUID,
        content: bytes,
    ) -> None:
        directory = self._document_directory(
            document_id,
        )

        if not directory.is_dir():
            raise AnalysisArtifactsNotFoundError(
                f"Артефакты document_id={document_id} не найдены.",
            )

        if not content:
            raise AnalysisArtifactStorageError(
                "Нельзя сохранить пустое ТЗ.",
            )

        path = directory / self._TECHNICAL_ASSIGNMENT_FILE

        try:
            self._write_bytes_atomic(
                path,
                content,
            )

        except OSError as error:
            raise AnalysisArtifactStorageError(
                "Не удалось сохранить файл ТЗ.",
            ) from error

    def _load_technical_assignment_sync(
        self,
        document_id: UUID,
    ) -> bytes | None:
        path = (
            self._document_directory(
                document_id,
            )
            / self._TECHNICAL_ASSIGNMENT_FILE
        )

        if not path.is_file():
            return None

        try:
            return path.read_bytes()

        except OSError as error:
            raise AnalysisArtifactStorageError(
                "Не удалось прочитать файл ТЗ.",
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
                use_explanatory_note=bool(
                    manifest.get(
                        "use_explanatory_note",
                        False,
                    )
                ),
                note_start_page=manifest.get(
                    "note_start_page",
                ),
                note_end_page=manifest.get(
                    "note_end_page",
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

    def _save_visualization_sync(
        self,
        document_id: UUID,
        pages: tuple[
            AnalysisPagePreview,
            ...,
        ],
    ) -> None:
        directory = self._document_directory(
            document_id,
        )

        if not (directory / self._MANIFEST_FILE).is_file():
            raise AnalysisArtifactsNotFoundError(
                f"Артефакты document_id={document_id} не найдены.",
            )

        if not pages:
            raise AnalysisArtifactStorageError(
                "Visualization artifact не содержит PDF-страниц.",
            )

        page_numbers = tuple(page.page_number for page in pages)

        if any(page_number < 1 for page_number in page_numbers) or len(
            set(page_numbers)
        ) != len(page_numbers):
            raise AnalysisArtifactStorageError(
                "Visualization artifact содержит некорректные номера страниц.",
            )

        target_directory = directory / self._VISUALIZATION_DIRECTORY

        temporary_directory = directory / (f".{self._VISUALIZATION_DIRECTORY}.tmp")

        try:
            shutil.rmtree(
                temporary_directory,
                ignore_errors=True,
            )

            temporary_directory.mkdir(
                parents=False,
                exist_ok=False,
            )

            manifest_pages: list[
                dict[
                    str,
                    Any,
                ]
            ] = []

            for page in pages:
                if page.width_points <= 0 or page.height_points <= 0:
                    raise ValueError(
                        "Visualization page dimensions должны быть положительными.",
                    )

                png_content = self._decode_png(
                    page.image_base64,
                )

                image_file = f"page-{page.page_number}.png"

                self._write_bytes_atomic(
                    temporary_directory / image_file,
                    png_content,
                )

                manifest_pages.append(
                    {
                        "page_number": page.page_number,
                        "width_points": page.width_points,
                        "height_points": page.height_points,
                        "image_file": image_file,
                        "extracted_text": page.extracted_text,
                        "text_words": [
                            {
                                "text": word.text,
                                "bbox": word.bbox.as_dict(),
                                "block_no": word.block_no,
                                "line_no": word.line_no,
                                "word_no": word.word_no,
                            }
                            for word in page.text_words
                        ],
                    }
                )

            self._write_json_atomic(
                temporary_directory / self._VISUALIZATION_MANIFEST_FILE,
                {
                    "schema_version": self._VISUALIZATION_SCHEMA_VERSION,
                    "pages": manifest_pages,
                },
            )

            if target_directory.exists():
                shutil.rmtree(
                    target_directory,
                )

            temporary_directory.replace(
                target_directory,
            )

        except (
            OSError,
            TypeError,
            ValueError,
            binascii.Error,
        ) as error:
            shutil.rmtree(
                temporary_directory,
                ignore_errors=True,
            )

            raise AnalysisArtifactStorageError(
                "Не удалось сохранить visualization artifact.",
            ) from error

    def _load_visualization_sync(
        self,
        document_id: UUID,
    ) -> (
        tuple[
            AnalysisPagePreview,
            ...,
        ]
        | None
    ):
        directory = (
            self._document_directory(
                document_id,
            )
            / self._VISUALIZATION_DIRECTORY
        )

        manifest_path = directory / self._VISUALIZATION_MANIFEST_FILE

        if not manifest_path.is_file():
            return None

        try:
            manifest = json.loads(
                manifest_path.read_text(
                    encoding="utf-8",
                )
            )

            if not isinstance(
                manifest,
                dict,
            ):
                raise ValueError(
                    "Visualization manifest must be JSON object.",
                )

            if (
                manifest.get(
                    "schema_version",
                )
                != self._VISUALIZATION_SCHEMA_VERSION
            ):
                raise ValueError(
                    "Unsupported visualization artifact schema version.",
                )

            raw_pages = manifest.get(
                "pages",
            )

            if (
                not isinstance(
                    raw_pages,
                    list,
                )
                or not raw_pages
            ):
                raise ValueError(
                    "Visualization manifest не содержит страниц.",
                )

            pages: list[AnalysisPagePreview] = []

            seen_pages: set[int] = set()

            for raw_page in raw_pages:
                if not isinstance(
                    raw_page,
                    dict,
                ):
                    raise ValueError(
                        "Visualization page metadata должен быть JSON object.",
                    )

                page_number = int(raw_page["page_number"])

                if page_number < 1 or page_number in seen_pages:
                    raise ValueError(
                        "Visualization manifest содержит некорректный page_number.",
                    )

                seen_pages.add(
                    page_number,
                )

                expected_image_file = f"page-{page_number}.png"

                if (
                    raw_page.get(
                        "image_file",
                    )
                    != expected_image_file
                ):
                    raise ValueError(
                        "Visualization manifest содержит неожиданный image_file.",
                    )

                image_path = directory / expected_image_file

                png_content = image_path.read_bytes()

                if not png_content.startswith(
                    self._PNG_SIGNATURE,
                ):
                    raise ValueError(
                        "Visualization PNG повреждён.",
                    )

                width_points = float(raw_page["width_points"])

                height_points = float(raw_page["height_points"])

                if width_points <= 0 or height_points <= 0:
                    raise ValueError(
                        "Visualization page dimensions должны быть положительными.",
                    )

                pages.append(
                    AnalysisPagePreview(
                        page_number=page_number,
                        width_points=width_points,
                        height_points=height_points,
                        image_base64=(
                            base64.b64encode(
                                png_content,
                            ).decode(
                                "ascii",
                            )
                        ),
                        extracted_text=str(
                            raw_page.get(
                                "extracted_text",
                                "",
                            )
                        ),
                        text_words=(
                            self._parse_text_words(
                                raw_page.get(
                                    "text_words",
                                    [],
                                )
                            )
                        ),
                    )
                )

            return tuple(
                pages,
            )

        except (
            KeyError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise AnalysisArtifactStorageError(
                "Не удалось прочитать visualization artifact.",
            ) from error

    @classmethod
    def _decode_png(
        cls,
        image_base64: str,
    ) -> bytes:
        """Декодирует и минимально проверяет PNG payload."""
        content = base64.b64decode(
            image_base64,
            validate=True,
        )

        if not content.startswith(
            cls._PNG_SIGNATURE,
        ):
            raise ValueError(
                "Visualization image должен быть PNG.",
            )

        return content

    @staticmethod
    def _parse_text_words(
        payload: object,
    ) -> tuple[
        AnalysisTextWord,
        ...,
    ]:
        """Восстанавливает PDF text geometry из manifest."""
        if not isinstance(
            payload,
            list,
        ):
            raise ValueError(
                "Visualization text_words должен быть JSON array.",
            )

        words: list[AnalysisTextWord] = []

        for item in payload:
            if not isinstance(
                item,
                dict,
            ):
                raise ValueError(
                    "Visualization text word должен быть JSON object.",
                )

            bbox = item.get(
                "bbox",
            )

            if not isinstance(
                bbox,
                dict,
            ):
                raise ValueError(
                    "Visualization text word не содержит bbox.",
                )

            words.append(
                AnalysisTextWord(
                    text=str(
                        item.get(
                            "text",
                            "",
                        )
                    ),
                    bbox=AnalysisBoundingBox(
                        x_min=int(bbox["x_min"]),
                        y_min=int(bbox["y_min"]),
                        x_max=int(bbox["x_max"]),
                        y_max=int(bbox["y_max"]),
                    ),
                    block_no=int(
                        item.get(
                            "block_no",
                            0,
                        )
                    ),
                    line_no=int(
                        item.get(
                            "line_no",
                            0,
                        )
                    ),
                    word_no=int(
                        item.get(
                            "word_no",
                            0,
                        )
                    ),
                )
            )

        return tuple(
            words,
        )

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

    @staticmethod
    def _write_bytes_atomic(
        path: Path,
        content: bytes,
    ) -> None:
        temporary_path = path.with_suffix(
            f"{path.suffix}.tmp",
        )

        temporary_path.write_bytes(
            content,
        )

        temporary_path.replace(
            path,
        )
