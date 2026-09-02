# services/knowledge-service/src/pdrd_knowledge_service/transport/http/schemas/normative_documents.py

"""HTTP schemas нормативных документов каталога."""

from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
)

from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeDocument,
)


class NormativeDocumentResponse(BaseModel):
    """Безопасная metadata нормативного документа для API."""

    model_config = ConfigDict(
        frozen=True,
    )

    document_id: UUID

    section_id: UUID

    category_id: UUID | None

    original_name: str

    mime_type: str

    size_bytes: int

    index_status: IndexingStatus

    index_error: str | None

    indexed_at: datetime | None

    ready_for_analysis: bool

    created_at: datetime

    updated_at: datetime

    @classmethod
    def from_domain(
        cls,
        document: NormativeDocument,
    ) -> "NormativeDocumentResponse":
        """Создаёт HTTP response без внутренних storage metadata."""
        return cls(
            document_id=document.document_id,
            section_id=document.section_id,
            category_id=document.category_id,
            original_name=document.original_name,
            mime_type=document.mime_type,
            size_bytes=document.size_bytes,
            index_status=document.index_status,
            index_error=document.index_error,
            indexed_at=document.indexed_at,
            ready_for_analysis=document.ready_for_analysis,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )
