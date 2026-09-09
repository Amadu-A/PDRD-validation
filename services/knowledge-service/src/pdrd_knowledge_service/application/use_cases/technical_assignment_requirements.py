# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/technical_assignment_requirements.py

"""Use case deterministic чтения atomic requirements ТЗ."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_knowledge_service.application.ports.technical_assignment_persistence import (
    TechnicalAssignmentUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.ports.technical_assignment_requirement_reader import (
    TechnicalAssignmentRequirementReader,
)
from pdrd_knowledge_service.application.use_cases.technical_assignments import (
    TechnicalAssignmentNotFoundError,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    TechnicalAssignmentIndexStatus,
)
from pdrd_knowledge_service.domain.technical_assignment_requirements import (
    TechnicalAssignmentRequirementList,
)

TECHNICAL_ASSIGNMENT_REQUIREMENT_DEFAULT_PAGE_SIZE = 100

TECHNICAL_ASSIGNMENT_REQUIREMENT_MAX_PAGE_SIZE = 200


class TechnicalAssignmentRequirementsConflictError(
    RuntimeError,
):
    """ТЗ существует, но atomic requirements ещё нельзя читать."""


@dataclass(frozen=True, slots=True)
class ListTechnicalAssignmentRequirements:
    """Возвращает T-R requirements без similarity search и GPU."""

    unit_of_work_factory: TechnicalAssignmentUnitOfWorkFactory

    reader: TechnicalAssignmentRequirementReader

    async def execute(
        self,
        *,
        technical_assignment_id: UUID,
        offset: int = 0,
        limit: int = TECHNICAL_ASSIGNMENT_REQUIREMENT_DEFAULT_PAGE_SIZE,
    ) -> TechnicalAssignmentRequirementList:
        """Читает deterministic page requirements READY ТЗ."""
        self._validate_page(
            offset=offset,
            limit=limit,
        )

        async with self.unit_of_work_factory() as unit_of_work:
            assignment = await unit_of_work.assignments.get(
                technical_assignment_id,
            )

        if assignment is None:
            raise TechnicalAssignmentNotFoundError(
                f"Техническое задание {technical_assignment_id} не найдено.",
            )

        if assignment.index_status is not TechnicalAssignmentIndexStatus.READY:
            raise TechnicalAssignmentRequirementsConflictError(
                "Atomic requirements ТЗ ещё недоступны: "
                f"{assignment.index_status.value}.",
            )

        requirements = await self.reader.list_requirements(
            technical_assignment_id=(assignment.technical_assignment_id),
            analysis_document_id=(assignment.analysis_document_id),
            section_id=assignment.section_id,
        )

        ordered = tuple(
            sorted(
                requirements,
                key=lambda requirement: (
                    requirement.requirement_index,
                    requirement.point_id,
                ),
            )
        )

        return TechnicalAssignmentRequirementList(
            technical_assignment_id=(assignment.technical_assignment_id),
            analysis_document_id=(assignment.analysis_document_id),
            section_id=assignment.section_id,
            source_file=assignment.original_name,
            source_sha256=assignment.sha256,
            total=len(
                ordered,
            ),
            offset=offset,
            limit=limit,
            requirements=ordered[offset : offset + limit],
        )

    @staticmethod
    def _validate_page(
        *,
        offset: int,
        limit: int,
    ) -> None:
        """Проверяет bounded internal pagination."""
        if offset < 0:
            raise ValueError(
                "offset должен быть неотрицательным.",
            )

        if limit < 1 or limit > TECHNICAL_ASSIGNMENT_REQUIREMENT_MAX_PAGE_SIZE:
            raise ValueError(
                "limit должен быть в диапазоне "
                "от 1 до "
                f"{TECHNICAL_ASSIGNMENT_REQUIREMENT_MAX_PAGE_SIZE}.",
            )
