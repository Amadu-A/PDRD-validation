# services/knowledge-service/tests/unit/test_technical_assignment_requirements.py

"""Unit tests deterministic atomic T requirement feed."""

from datetime import (
    UTC,
    datetime,
)
from uuid import (
    UUID,
    uuid4,
)

import pytest
from pdrd_knowledge_service.application.use_cases.technical_assignment_requirements import (
    ListTechnicalAssignmentRequirements,
    TechnicalAssignmentRequirementsConflictError,
)
from pdrd_knowledge_service.application.use_cases.technical_assignments import (
    TechnicalAssignmentNotFoundError,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    PDF_MIME_TYPE,
    TechnicalAssignment,
    TechnicalAssignmentIndexStatus,
)
from pdrd_knowledge_service.domain.technical_assignment_requirements import (
    TechnicalAssignmentRequirementSource,
)

NOW = datetime(
    2026,
    9,
    9,
    13,
    30,
    tzinfo=UTC,
)


def _assignment(
    *,
    technical_assignment_id: UUID,
    analysis_document_id: UUID,
    section_id: UUID,
    status: TechnicalAssignmentIndexStatus = (TechnicalAssignmentIndexStatus.READY),
) -> TechnicalAssignment:
    """Создаёт T lifecycle для теста."""
    return TechnicalAssignment(
        technical_assignment_id=(technical_assignment_id),
        analysis_document_id=(analysis_document_id),
        section_id=section_id,
        original_name="ТЗ.pdf",
        mime_type=PDF_MIME_TYPE,
        size_bytes=2048,
        sha256="a" * 64,
        index_status=status,
        index_error=None,
        indexed_at=(NOW if status is TechnicalAssignmentIndexStatus.READY else None),
        created_at=NOW,
        updated_at=NOW,
    )


def _requirement(
    *,
    technical_assignment_id: UUID,
    analysis_document_id: UUID,
    section_id: UUID,
    requirement_index: int,
) -> TechnicalAssignmentRequirementSource:
    """Создаёт indexed atomic requirement."""
    return TechnicalAssignmentRequirementSource(
        point_id=str(
            uuid4(),
        ),
        requirement_id=(f"T-R{requirement_index}"),
        requirement_index=requirement_index,
        technical_assignment_id=str(
            technical_assignment_id,
        ),
        analysis_document_id=str(
            analysis_document_id,
        ),
        section_id=str(
            section_id,
        ),
        source_file="ТЗ.pdf",
        source_sha256="a" * 64,
        page=requirement_index,
        strength="candidate",
        scopes=("thermal",),
        normative_refs=(),
        source_text=(f"Требование {requirement_index}."),
        text=(f"Требование {requirement_index}."),
    )


class _FakeAssignmentRepository:
    """Fake repository T lifecycle."""

    def __init__(
        self,
        assignment: TechnicalAssignment | None,
    ) -> None:
        """Сохраняет assignment."""
        self.assignment = assignment

    async def get(
        self,
        technical_assignment_id: UUID,
    ) -> TechnicalAssignment | None:
        """Возвращает assignment при совпадении ID."""
        if self.assignment is not None and (
            self.assignment.technical_assignment_id == technical_assignment_id
        ):
            return self.assignment

        return None


class _FakeUnitOfWork:
    """Fake UoW только для read use case."""

    def __init__(
        self,
        assignment: TechnicalAssignment | None,
    ) -> None:
        """Создаёт repository."""
        self.assignments = _FakeAssignmentRepository(
            assignment,
        )

    async def __aenter__(
        self,
    ) -> "_FakeUnitOfWork":
        """Входит в context."""
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        """Выходит из context."""
        del exc_type
        del exc_value
        del traceback


class _FakeReader:
    """Fake requirement reader."""

    def __init__(
        self,
        requirements: tuple[
            TechnicalAssignmentRequirementSource,
            ...,
        ],
    ) -> None:
        """Сохраняет result."""
        self.requirements = requirements

        self.calls: list[
            tuple[
                UUID,
                UUID,
                UUID,
            ]
        ] = []

    async def list_requirements(
        self,
        *,
        technical_assignment_id: UUID,
        analysis_document_id: UUID,
        section_id: UUID,
    ) -> tuple[
        TechnicalAssignmentRequirementSource,
        ...,
    ]:
        """Возвращает configured requirements."""
        self.calls.append(
            (
                technical_assignment_id,
                analysis_document_id,
                section_id,
            )
        )

        return self.requirements


@pytest.mark.asyncio
async def test_requirement_feed_is_sorted_and_paginated() -> None:
    """T-first feed не зависит от Qdrant point order."""
    technical_assignment_id = uuid4()

    analysis_document_id = uuid4()

    section_id = uuid4()

    assignment = _assignment(
        technical_assignment_id=technical_assignment_id,
        analysis_document_id=analysis_document_id,
        section_id=section_id,
    )

    reader = _FakeReader(
        (
            _requirement(
                technical_assignment_id=(technical_assignment_id),
                analysis_document_id=(analysis_document_id),
                section_id=section_id,
                requirement_index=3,
            ),
            _requirement(
                technical_assignment_id=(technical_assignment_id),
                analysis_document_id=(analysis_document_id),
                section_id=section_id,
                requirement_index=1,
            ),
            _requirement(
                technical_assignment_id=(technical_assignment_id),
                analysis_document_id=(analysis_document_id),
                section_id=section_id,
                requirement_index=2,
            ),
        )
    )

    use_case = ListTechnicalAssignmentRequirements(
        unit_of_work_factory=lambda: _FakeUnitOfWork(
            assignment,
        ),
        reader=reader,
    )

    result = await use_case.execute(
        technical_assignment_id=(technical_assignment_id),
        offset=1,
        limit=1,
    )

    assert result.source_sha256 == "a" * 64

    assert result.total == 3

    assert result.offset == 1

    assert result.limit == 1

    assert [requirement.requirement_id for requirement in result.requirements] == [
        "T-R2",
    ]

    assert reader.calls == [
        (
            technical_assignment_id,
            analysis_document_id,
            section_id,
        )
    ]


@pytest.mark.asyncio
async def test_requirement_feed_requires_ready_assignment() -> None:
    """Не-READY ТЗ не даёт partial T-first feed."""
    technical_assignment_id = uuid4()

    assignment = _assignment(
        technical_assignment_id=(technical_assignment_id),
        analysis_document_id=uuid4(),
        section_id=uuid4(),
        status=(TechnicalAssignmentIndexStatus.INDEXING),
    )

    reader = _FakeReader(
        (),
    )

    use_case = ListTechnicalAssignmentRequirements(
        unit_of_work_factory=lambda: _FakeUnitOfWork(
            assignment,
        ),
        reader=reader,
    )

    with pytest.raises(
        TechnicalAssignmentRequirementsConflictError,
    ):
        await use_case.execute(
            technical_assignment_id=(technical_assignment_id),
        )

    assert reader.calls == []


@pytest.mark.asyncio
async def test_requirement_feed_reports_missing_assignment() -> None:
    """Unknown T ID остаётся application 404."""
    use_case = ListTechnicalAssignmentRequirements(
        unit_of_work_factory=lambda: _FakeUnitOfWork(
            None,
        ),
        reader=_FakeReader(
            (),
        ),
    )

    with pytest.raises(
        TechnicalAssignmentNotFoundError,
    ):
        await use_case.execute(
            technical_assignment_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_requirement_feed_rejects_unbounded_limit() -> None:
    """Internal route не может вернуть unbounded response."""
    technical_assignment_id = uuid4()

    assignment = _assignment(
        technical_assignment_id=(technical_assignment_id),
        analysis_document_id=uuid4(),
        section_id=uuid4(),
    )

    use_case = ListTechnicalAssignmentRequirements(
        unit_of_work_factory=lambda: _FakeUnitOfWork(
            assignment,
        ),
        reader=_FakeReader(
            (),
        ),
    )

    with pytest.raises(
        ValueError,
    ):
        await use_case.execute(
            technical_assignment_id=(technical_assignment_id),
            limit=201,
        )
