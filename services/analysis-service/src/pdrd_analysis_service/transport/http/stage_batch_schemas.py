# services/analysis-service/src/pdrd_analysis_service/transport/http/stage_batch_schemas.py

"""HTTP schemas stage-scoped GPU processing нескольких PDF-страниц."""

from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from pdrd_analysis_service.transport.http.context_pool import restore_context_pool
from pdrd_analysis_service.transport.http.normative_corpus_schemas import (
    NormativeCorpusScope,
)
from pdrd_analysis_service.transport.http.schemas import (
    CheckNormsRequest,
    CheckNormsResponse,
    FinalizeRequest,
    FinalizeResponse,
    PageFactsPayload,
    UnderstandPageRequest,
    UnderstandPageResponse,
)
from pdrd_analysis_service.transport.http.technical_assignment_schemas import (
    CheckTechnicalAssignmentResponse,
    TechnicalAssignmentRequirementPayload,
)


class UnderstandPagesStageRequest(
    BaseModel,
):
    """Все PDF-страницы одного page-understanding GPU stage."""

    model_config = ConfigDict(
        extra="forbid",
    )

    document_id: UUID

    items: list[UnderstandPageRequest] = Field(
        min_length=1,
    )


class UnderstandPagesStageItemResponse(
    BaseModel,
):
    """Результат understanding одной физической страницы."""

    page_number: int = Field(
        ge=1,
    )

    result: UnderstandPageResponse


class UnderstandPagesStageResponse(
    BaseModel,
):
    """Ordered результаты page-understanding stage."""

    items: list[UnderstandPagesStageItemResponse]
    cancelled: bool = False


class TechnicalAssignmentStagePageRequest(
    BaseModel,
):
    """Page-local часть T-first stage."""

    model_config = ConfigDict(
        extra="forbid",
    )

    page_number: int = Field(
        ge=1,
    )

    extracted_text: str

    page_facts: PageFactsPayload

    image_base64: str = Field(
        min_length=1,
    )


class CheckTechnicalAssignmentStageRequest(
    BaseModel,
):
    """T-first stage с общим requirement feed и всеми PDF-страницами."""

    model_config = ConfigDict(
        extra="forbid",
    )

    document_id: UUID

    technical_assignment_id: UUID

    analysis_document_id: UUID

    section_id: UUID

    source_file: str = Field(
        min_length=1,
    )

    source_sha256: str = Field(
        min_length=64,
        max_length=64,
    )

    requirements: list[TechnicalAssignmentRequirementPayload] = Field(
        min_length=1,
    )

    items: list[TechnicalAssignmentStagePageRequest] = Field(
        min_length=1,
    )


class CheckTechnicalAssignmentStageItemResponse(
    BaseModel,
):
    """Результат T-first одной страницы."""

    page_number: int = Field(
        ge=1,
    )

    result: CheckTechnicalAssignmentResponse


class CheckTechnicalAssignmentStageResponse(
    BaseModel,
):
    """Ordered результаты T-first stage."""

    items: list[CheckTechnicalAssignmentStageItemResponse]
    cancelled: bool = False


class CheckNormsStageRequest(
    BaseModel,
):
    """Все подготовленные страницы одного normative-check GPU stage."""

    model_config = ConfigDict(
        extra="forbid",
    )

    document_id: UUID

    items: list[CheckNormsRequest] = Field(
        min_length=1,
    )
    normative_corpus: NormativeCorpusScope | None = None

    @model_validator(mode="before")
    @classmethod
    def restore_context(cls, value):
        """Возвращает исходные строки до строгой валидации страниц и источников."""
        return restore_context_pool(value)


class CheckNormsStageItemResponse(
    BaseModel,
):
    """Normative check одной страницы."""

    page_number: int = Field(
        ge=1,
    )

    result: CheckNormsResponse


class CheckNormsStageResponse(
    BaseModel,
):
    """Ordered результаты normative-check stage."""

    items: list[CheckNormsStageItemResponse]
    cancelled: bool = False


class FinalizeStageItemRequest(
    BaseModel,
):
    """Finalization input одной физической страницы."""

    model_config = ConfigDict(
        extra="forbid",
    )

    page_number: int = Field(
        ge=1,
    )

    request: FinalizeRequest


class FinalizeStageRequest(
    BaseModel,
):
    """Все страницы одного finalization GPU stage."""

    model_config = ConfigDict(
        extra="forbid",
    )

    document_id: UUID

    items: list[FinalizeStageItemRequest] = Field(
        min_length=1,
    )


class FinalizeStageItemResponse(
    BaseModel,
):
    """Finalization result одной страницы."""

    page_number: int = Field(
        ge=1,
    )

    result: FinalizeResponse


class FinalizeStageResponse(
    BaseModel,
):
    """Ordered результаты finalization stage."""

    items: list[FinalizeStageItemResponse]
    cancelled: bool = False
