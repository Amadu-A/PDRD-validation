# services/api-gateway/src/pdrd_api_gateway/application/ports/artifacts.py

"""Application port хранения файлов и результатов анализа."""

from dataclasses import dataclass
from typing import (
    Any,
    Protocol,
)
from uuid import UUID

from pdrd_api_gateway.domain.analysis_submission import (
    AnalysisSubmission,
)
from pdrd_api_gateway.domain.normative_snapshot import (
    NormativeAnalysisSnapshot,
)


class AnalysisArtifactStorageError(RuntimeError):
    """Ошибка infrastructure-хранилища артефактов анализа."""


class AnalysisArtifactsNotFoundError(
    AnalysisArtifactStorageError,
):
    """Артефакты указанного document_id не найдены."""


@dataclass(frozen=True, slots=True)
class AnalysisRequestArtifacts:
    """Сохранённая заявка вместе с исходными байтами файлов."""

    submission: AnalysisSubmission

    pdf_content: bytes | None
    cad_content: bytes | None

    normative_snapshot: NormativeAnalysisSnapshot | None = None


class AnalysisArtifactStore(Protocol):
    """Контракт хранения входных файлов и результата анализа."""

    async def save_request(
        self,
        *,
        submission: AnalysisSubmission,
        pdf_content: bytes | None,
        cad_content: bytes | None,
    ) -> None:
        """Сохраняет manifest и исходные пользовательские файлы."""
        ...

    async def load_request(
        self,
        *,
        document_id: UUID,
    ) -> AnalysisRequestArtifacts:
        """Загружает ранее сохранённую заявку."""
        ...

    async def delete_request(
        self,
        *,
        document_id: UUID,
    ) -> None:
        """Удаляет сохранённую заявку и связанные артефакты."""
        ...

    async def save_result(
        self,
        *,
        document_id: UUID,
        result: dict[
            str,
            Any,
        ],
    ) -> None:
        """Сохраняет итоговый JSON анализа."""
        ...

    async def load_result(
        self,
        *,
        document_id: UUID,
    ) -> (
        dict[
            str,
            Any,
        ]
        | None
    ):
        """Возвращает итоговый JSON, если он уже сформирован."""
        ...
