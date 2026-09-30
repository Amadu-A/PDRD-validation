# services/experience-service/src/pdrd_experience_service/application/use_cases/select_experience.py

"""Собирает пригодные Experience-примеры, пока не сохраняя их в БД."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_experience_service.application.ports.confirmed_areas import (
    ConfirmedAreasReader,
)
from pdrd_experience_service.application.ports.review import (
    ReviewRepository,
)
from pdrd_experience_service.domain.experience_selection import (
    ExperienceCandidate,
    select_experience_candidates,
)


@dataclass(frozen=True, slots=True)
class SelectExperience:
    """Сверяет утверждённый review с серверными подтверждениями областей."""

    reviews: ReviewRepository
    areas: ConfirmedAreasReader

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> tuple[ExperienceCandidate, ...]:
        """Возвращает примеры с provenance, без побочных эффектов."""
        session = await self.reviews.load(job_id)

        if session is None:
            raise LookupError(f"Human Review для job {job_id} не найден.")

        # Сначала проверяем утверждение, чтобы не запрашивать лишние области.
        session.accepted_for_pdf()

        confirmed = await self.areas.load_confirmed(
            job_id=job_id,
        )

        return select_experience_candidates(
            session=session,
            confirmed_areas=confirmed,
        )
