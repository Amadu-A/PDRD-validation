# services/experience-service/src/pdrd_experience_service/infrastructure/analysis/completed_reader.py

"""Проверяет серверные артефакты перед открытием Human Review.

Назначение файла:
- реализовать существующий application port CompletedAnalysisReader;
- проверить принадлежность артефактов запрошенному заданию;
- убедиться, что серверный SHA-256 соответствует исходному PDF;
- запретить открытие Review из незавершённого анализа;
- передать проверенные данные существующему mapper визуализации.

Важно: proposed_regions остаются только предложениями VLM.
Данный адаптер не создаёт ConfirmedFindingArea и не подтверждает
координаты от имени инженера.
"""

import hashlib
from dataclasses import dataclass
from uuid import UUID

from pdrd_experience_service.application.ports.analysis_source import (
    AnalysisSourceReader,
    CompletedAnalysisArtifacts,
)
from pdrd_experience_service.application.ports.review import (
    CompletedAnalysis,
)
from pdrd_experience_service.domain.review import ReviewError
from pdrd_experience_service.infrastructure.analysis.visualization import (
    completed_analysis_from_visualization,
)


def _check_optional_identity(
    payload: dict[str, object],
    *,
    field: str,
    expected: UUID,
) -> None:
    """Проверяет дополнительный идентификатор, если он есть в артефакте.

    Не все исторические результаты анализа содержат собственные
    job_id/document_id. Однако явно присутствующий идентификатор
    никогда не должен противоречить идентичности задания.
    """
    value = payload.get(field)

    if value is not None and str(value) != str(expected):
        raise ReviewError("Артефакт анализа содержит идентификатор другого задания.")


@dataclass(frozen=True, slots=True)
class VerifiedCompletedAnalysisReader:
    """Адаптирует проверенный серверный набор к контракту OpenReview."""

    source: AnalysisSourceReader

    async def load(
        self,
        job_id: UUID,
    ) -> CompletedAnalysis:
        """Формирует исходные находки только из завершённого PDF-задания.

        Проверки выполняются до преобразования визуализации.
        При любой ошибке верификации Review не должен открываться.
        """
        artifacts = await self.source.load_completed(
            job_id,
        )

        self._check_artifacts(
            requested_job_id=job_id,
            artifacts=artifacts,
        )

        completed = completed_analysis_from_visualization(
            job_id=job_id,
            document_id=artifacts.document_id,
            source_filename=artifacts.source_filename,
            pdf_content=artifacts.pdf_content,
            result=artifacts.result,
            visualization=artifacts.visualization,
        )

        if not completed.rendered_pages:
            raise ReviewError(
                "Нельзя открыть Human Review без серверных листов визуализации."
            )

        if completed.source_sha256 != artifacts.source_sha256:
            raise ReviewError("Контрольная сумма исходного документа не совпадает.")

        return completed

    @staticmethod
    def _check_artifacts(
        *,
        requested_job_id: UUID,
        artifacts: CompletedAnalysisArtifacts,
    ) -> None:
        """Проверяет идентичность, статус и содержимое исходных данных."""
        if artifacts.job_id != requested_job_id:
            raise ReviewError("Источник вернул данные другого задания анализа.")

        if not isinstance(artifacts.document_id, UUID):
            raise ReviewError("Отсутствует подтверждённый идентификатор документа.")

        if artifacts.status != "completed":
            raise LookupError("Human Review доступен только для завершённого анализа.")

        if not isinstance(
            artifacts.pdf_content, bytes
        ) or not artifacts.pdf_content.startswith(b"%PDF-"):
            raise ReviewError("Для текущего адаптера требуется исходный PDF.")

        actual_digest = hashlib.sha256(
            artifacts.pdf_content,
        ).hexdigest()

        if actual_digest != artifacts.source_sha256:
            raise ReviewError(
                "SHA-256 исходного PDF не соответствует серверным метаданным."
            )

        if not isinstance(artifacts.result, dict) or not isinstance(
            artifacts.visualization, dict
        ):
            raise ReviewError(
                "Результат анализа или визуализация имеют неверный формат."
            )

        _check_optional_identity(
            artifacts.result,
            field="job_id",
            expected=requested_job_id,
        )

        _check_optional_identity(
            artifacts.result,
            field="document_id",
            expected=artifacts.document_id,
        )
