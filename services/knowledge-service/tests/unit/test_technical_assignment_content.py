# services/knowledge-service/tests/unit/test_technical_assignment_content.py

"""Unit tests browser-viewable T content."""

from datetime import (
    UTC,
    datetime,
)
from uuid import uuid4

from pdrd_knowledge_service.application.use_cases.technical_assignments import (
    GetTechnicalAssignment,
    GetTechnicalAssignmentContent,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    TechnicalAssignment,
    TechnicalAssignmentIndexStatus,
)

NOW = datetime(
    2026,
    9,
    7,
    12,
    0,
    tzinfo=UTC,
)


class FakeAssignmentRepository:
    """Fake T repository."""

    def __init__(
        self,
        assignment: TechnicalAssignment,
    ) -> None:
        """Сохраняет assignment."""
        self.assignment = assignment

    async def get(
        self,
        technical_assignment_id,
    ):
        """Возвращает assignment."""
        if technical_assignment_id == self.assignment.technical_assignment_id:
            return self.assignment

        return None


class FakeUnitOfWork:
    """Fake T unit of work."""

    def __init__(
        self,
        assignment: TechnicalAssignment,
    ) -> None:
        """Создаёт repository."""
        self.assignments = FakeAssignmentRepository(
            assignment,
        )

    async def __aenter__(
        self,
    ):
        """Входит в context."""
        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        """Выходит из context."""


class FakeStorage:
    """Fake binary storage."""

    def __init__(
        self,
        content: bytes,
    ) -> None:
        """Сохраняет bytes."""
        self.content = content

    async def read(
        self,
        *,
        storage_key: str,
    ) -> bytes:
        """Возвращает bytes."""
        assert storage_key

        return self.content


class FakeConverter:
    """Fake Word -> PDF converter."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует counter."""
        self.calls = 0

    async def convert_to_pdf(
        self,
        *,
        content: bytes,
        original_name: str,
    ) -> bytes:
        """Возвращает deterministic PDF."""
        assert content
        assert original_name

        self.calls += 1

        return b"%PDF-preview"


def make_assignment(
    *,
    source_file: str,
    mime_type: str,
) -> TechnicalAssignment:
    """Создаёт READY ТЗ."""
    return TechnicalAssignment(
        technical_assignment_id=uuid4(),
        analysis_document_id=uuid4(),
        section_id=uuid4(),
        original_name=source_file,
        mime_type=mime_type,
        size_bytes=100,
        sha256="a" * 64,
        index_status=TechnicalAssignmentIndexStatus.READY,
        index_error=None,
        indexed_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


async def test_pdf_content_is_returned_without_conversion() -> None:
    """PDF ТЗ отдаётся напрямую."""
    assignment = make_assignment(
        source_file="ТЗ.pdf",
        mime_type=PDF_MIME_TYPE,
    )

    converter = FakeConverter()

    use_case = GetTechnicalAssignmentContent(
        get_technical_assignment=GetTechnicalAssignment(
            unit_of_work_factory=lambda: FakeUnitOfWork(
                assignment,
            ),
        ),
        storage=FakeStorage(
            b"%PDF-original",
        ),
        office_converter=converter,
    )

    result = await use_case.execute(
        technical_assignment_id=assignment.technical_assignment_id,
    )

    assert result.content == b"%PDF-original"

    assert result.mime_type == PDF_MIME_TYPE

    assert converter.calls == 0


async def test_docx_content_is_converted_to_pdf_preview() -> None:
    """Word ТЗ получает PDF-preview."""
    assignment = make_assignment(
        source_file="ТЗ.docx",
        mime_type=DOCX_MIME_TYPE,
    )

    converter = FakeConverter()

    use_case = GetTechnicalAssignmentContent(
        get_technical_assignment=GetTechnicalAssignment(
            unit_of_work_factory=lambda: FakeUnitOfWork(
                assignment,
            ),
        ),
        storage=FakeStorage(
            b"word-content",
        ),
        office_converter=converter,
    )

    result = await use_case.execute(
        technical_assignment_id=assignment.technical_assignment_id,
    )

    assert result.content == b"%PDF-preview"

    assert result.mime_type == PDF_MIME_TYPE

    assert converter.calls == 1
