# services/api-gateway/src/pdrd_api_gateway/domain/analysis_job.py

"""Domain-модель асинхронного задания анализа документа."""

from dataclasses import (
    dataclass,
    field,
)
from datetime import (
    UTC,
    datetime,
)
from enum import StrEnum
from typing import ClassVar
from uuid import (
    UUID,
    uuid4,
)

from pdrd_api_gateway.domain.normative_snapshot import (
    NormativeAnalysisSnapshot,
)


class AnalysisJobStatus(StrEnum):
    """Допустимые состояния задания анализа."""

    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisProgressStage(StrEnum):
    """Стабильные пользовательские этапы analysis pipeline."""

    EXTRACTING_SOURCES = "extracting_sources"
    PREPARING_CONTEXT = "preparing_context"
    UNDERSTANDING_SHEET = "understanding_sheet"
    RETRIEVING_REQUIREMENTS = "retrieving_requirements"
    CHECKING_REQUIREMENTS = "checking_requirements"
    ENRICHING_FINDINGS = "enriching_findings"
    FINALIZING_FINDINGS = "finalizing_findings"
    BUILDING_RESULT = "building_result"

    @property
    def current(
        self,
    ) -> int:
        """Возвращает порядковый номер этапа."""
        return _PROGRESS_STAGE_ORDER[self]

    @property
    def message(
        self,
    ) -> str:
        """Возвращает человекочитаемое описание этапа."""
        return _PROGRESS_STAGE_MESSAGES[self]


ANALYSIS_PROGRESS_TOTAL = 8

_PROGRESS_STAGE_ORDER = {
    AnalysisProgressStage.EXTRACTING_SOURCES: 1,
    AnalysisProgressStage.PREPARING_CONTEXT: 2,
    AnalysisProgressStage.UNDERSTANDING_SHEET: 3,
    AnalysisProgressStage.RETRIEVING_REQUIREMENTS: 4,
    AnalysisProgressStage.CHECKING_REQUIREMENTS: 5,
    AnalysisProgressStage.ENRICHING_FINDINGS: 6,
    AnalysisProgressStage.FINALIZING_FINDINGS: 7,
    AnalysisProgressStage.BUILDING_RESULT: 8,
}

_PROGRESS_STAGE_MESSAGES = {
    AnalysisProgressStage.EXTRACTING_SOURCES: (
        "Извлекаю данные из загруженных документов…"
    ),
    AnalysisProgressStage.PREPARING_CONTEXT: (
        "Готовлю контекст проекта и исходные данные…"
    ),
    AnalysisProgressStage.UNDERSTANDING_SHEET: (
        "Разбираю структуру, объекты и связи на листе…"
    ),
    AnalysisProgressStage.RETRIEVING_REQUIREMENTS: (
        "Подбираю применимые нормативные требования…"
    ),
    AnalysisProgressStage.CHECKING_REQUIREMENTS: (
        "Сверяю проектное решение с требованиями…"
    ),
    AnalysisProgressStage.ENRICHING_FINDINGS: (
        "Уточняю нормативные основания замечаний…"
    ),
    AnalysisProgressStage.FINALIZING_FINDINGS: ("Формирую итоговые замечания…"),
    AnalysisProgressStage.BUILDING_RESULT: ("Собираю итоговый результат анализа…"),
}


class InvalidAnalysisJobTransitionError(ValueError):
    """Ошибка недопустимого перехода между состояниями задания."""


def utc_now() -> datetime:
    """Возвращает текущее время UTC с информацией о timezone."""
    return datetime.now(
        UTC,
    )


@dataclass(slots=True)
class AnalysisJob:
    """Представляет одно пользовательское задание анализа."""

    _ALLOWED_TRANSITIONS: ClassVar[
        dict[
            AnalysisJobStatus,
            frozenset[AnalysisJobStatus],
        ]
    ] = {
        AnalysisJobStatus.PENDING: frozenset(
            {
                AnalysisJobStatus.QUEUED,
                AnalysisJobStatus.FAILED,
                AnalysisJobStatus.CANCELLED,
            }
        ),
        AnalysisJobStatus.QUEUED: frozenset(
            {
                AnalysisJobStatus.PROCESSING,
                AnalysisJobStatus.FAILED,
                AnalysisJobStatus.CANCELLED,
            }
        ),
        AnalysisJobStatus.PROCESSING: frozenset(
            {
                AnalysisJobStatus.QUEUED,
                AnalysisJobStatus.COMPLETED,
                AnalysisJobStatus.FAILED,
                AnalysisJobStatus.CANCELLED,
            }
        ),
        AnalysisJobStatus.COMPLETED: frozenset(),
        AnalysisJobStatus.FAILED: frozenset(),
        AnalysisJobStatus.CANCELLED: frozenset(),
    }

    id: UUID

    document_id: UUID | None = None

    normative_snapshot: NormativeAnalysisSnapshot | None = None

    status: AnalysisJobStatus = AnalysisJobStatus.PENDING

    progress_stage: AnalysisProgressStage | None = None

    attempt_count: int = 0

    error_code: str | None = None
    error_message: str | None = None

    created_at: datetime = field(
        default_factory=utc_now,
    )

    updated_at: datetime = field(
        default_factory=utc_now,
    )

    @classmethod
    def create(
        cls,
        *,
        document_id: UUID | None = None,
        normative_snapshot: NormativeAnalysisSnapshot | None = None,
    ) -> "AnalysisJob":
        """Создаёт новое задание в состоянии pending."""
        return cls(
            id=uuid4(),
            document_id=document_id,
            normative_snapshot=normative_snapshot,
        )

    def mark_queued(
        self,
    ) -> None:
        """Помечает успешно опубликованное в очереди задание."""
        self._transition_to(
            AnalysisJobStatus.QUEUED,
        )

    def mark_processing(
        self,
    ) -> None:
        """Помечает задание как выполняемое worker-ом."""
        self._transition_to(
            AnalysisJobStatus.PROCESSING,
        )

        self.attempt_count += 1
        self.error_code = None
        self.error_message = None
        self.progress_stage = None

    def resume_processing_attempt(
        self,
    ) -> None:
        """Учитывает broker redelivery уже processing задания."""
        if self.status is not AnalysisJobStatus.PROCESSING:
            raise InvalidAnalysisJobTransitionError(
                "Повтор processing attempt допустим только из processing.",
            )

        self.attempt_count += 1
        self.error_code = None
        self.error_message = None
        self.progress_stage = None
        self.updated_at = utc_now()

    def update_progress(
        self,
        *,
        stage: AnalysisProgressStage,
    ) -> bool:
        """Обновляет progress только вперёд.

        Late callback предыдущего этапа не имеет права откатить
        пользовательский progress назад.
        """
        if self.status is not AnalysisJobStatus.PROCESSING:
            return False

        if (
            self.progress_stage is not None
            and stage.current < self.progress_stage.current
        ):
            return False

        self.progress_stage = stage
        self.updated_at = utc_now()

        return True

    def mark_requeued(
        self,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        """Возвращает transient/stale processing job в durable queue."""
        self._transition_to(
            AnalysisJobStatus.QUEUED,
        )

        self.progress_stage = None
        self.error_code = error_code[:128]
        self.error_message = error_message[:2000]

    def touch_processing(
        self,
        *,
        changed_at: datetime | None = None,
    ) -> None:
        """Обновляет heartbeat только у processing задания."""
        if self.status is not AnalysisJobStatus.PROCESSING:
            return

        self.updated_at = changed_at or utc_now()

    def mark_completed(
        self,
    ) -> None:
        """Помечает успешно завершённое задание."""
        self._transition_to(
            AnalysisJobStatus.COMPLETED,
        )

        self.error_code = None
        self.error_message = None

    def mark_failed(
        self,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        """Помечает задание как завершённое с ошибкой."""
        self._transition_to(
            AnalysisJobStatus.FAILED,
        )

        self.error_code = error_code[:128]
        self.error_message = error_message[:2000]

    def mark_cancelled(
        self,
    ) -> None:
        """Помечает отменённое задание."""
        self._transition_to(
            AnalysisJobStatus.CANCELLED,
        )

    def _transition_to(
        self,
        target_status: AnalysisJobStatus,
    ) -> None:
        """Выполняет разрешённый lifecycle transition."""
        allowed_statuses = self._ALLOWED_TRANSITIONS[self.status]

        if target_status not in allowed_statuses:
            raise InvalidAnalysisJobTransitionError(
                "Недопустимый переход задания анализа: "
                f"{self.status.value} -> {target_status.value}.",
            )

        self.status = target_status
        self.updated_at = utc_now()
