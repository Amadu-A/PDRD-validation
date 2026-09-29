# services/experience-service/src/pdrd_experience_service/application/use_cases/approval_experience.py

"""Перенос утверждённого Review с явным результатом и безопасным повтором.

Операционный Review уже сохранён до crop. Его утверждение не откатывается
при недоступности Document Service. Ошибка явно возвращается интерфейсу;
повторный CaptureExperience идемпотентно завершает перенос.
"""

from dataclasses import dataclass

from pdrd_experience_service.application.use_cases.capture_experience import (
    CaptureExperience,
)
from pdrd_experience_service.domain.review import ReviewSession


@dataclass(frozen=True, slots=True)
class ApprovalExperience:
    """Автоматический перенос при утверждении, без фоновых незавершённых promises."""

    capture: CaptureExperience

    async def execute(self, *, review: ReviewSession, actor: str) -> dict:
        """Результат не утверждает сохранение Experience, если crop/SQL не завершены."""
        try:
            result = await self.capture.execute(
                job_id=review.job_id, expected_revision=review.revision, actor=actor
            )
            return {"status": "saved", **result}
        except Exception:
            # Утверждение уже зафиксировано: любой сбой переноса сообщаем явно,
            # не выдавая выполненную команду Review за потерянную сетевую запись.
            return {
                "status": "error",
                "message": (
                    "Review утверждён. Перенос в Experience не завершён; "
                    "повторите скачивание итогового PDF, чтобы завершить сохранение."
                ),
            }
