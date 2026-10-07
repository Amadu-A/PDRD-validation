# services/analysis-service/src/pdrd_analysis_service/transport/http/document_context_routes.py

"""Закрытые HTTP-этапы D-контекста и межстраничной проверки."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from pdrd_analysis_service.application.ports.vision_model import VisionModelError
from pdrd_analysis_service.application.use_cases.document_context import (
    CrossPageCancelled,
    DocumentContextOptions,
    DocumentPage,
    build_page_document_context,
)
from pdrd_analysis_service.core.container import ApplicationContainer
from pdrd_analysis_service.transport.http.dependencies import get_container
from pdrd_analysis_service.transport.http.schemas import (
    FindingDraftPayload,
    PageFactsPayload,
)

router = APIRouter(prefix="/internal/v1/document-context", tags=["document-context"])


class DocumentPagePayload(BaseModel):
    """Страница и факты из существующего Understanding Stage."""

    model_config = ConfigDict(extra="forbid")
    page_number: int = Field(ge=1)
    extracted_text: str
    page_facts: PageFactsPayload
    text_words: list[dict[str, Any]] = Field(default_factory=list)

    def to_domain(self) -> DocumentPage:
        """Преобразует HTTP payload в application DTO."""
        facts = self.page_facts.to_domain()
        return DocumentPage(
            page=self.page_number,
            text=self.extracted_text,
            summary=facts.summary,
            facts=facts.document_facts,
            text_words=tuple(self.text_words),
        )


class SemanticPagePayload(BaseModel):
    """Результат Qdrant поиска для одной страницы."""

    model_config = ConfigDict(extra="forbid")
    page_number: int = Field(ge=1)
    sources: list[dict[str, Any]] = Field(default_factory=list)


class BuildPageContextsRequest(BaseModel):
    """Полный индекс фактов и короткие семантические результаты страниц."""

    model_config = ConfigDict(extra="forbid")
    pages: list[DocumentPagePayload] = Field(min_length=1, max_length=200)
    semantic: list[SemanticPagePayload] = Field(default_factory=list)


class PageContextPayload(BaseModel):
    """Ограниченный D-контекст одного листа."""

    page_number: int
    sources: list[dict[str, Any]]


class BuildPageContextsResponse(BaseModel):
    """Ordered выдача D-источников всех листов."""

    items: list[PageContextPayload]


class CheckConsistencyRequest(BaseModel):
    """Отдельный document-scoped этап после page-local проверки."""

    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    pages: list[DocumentPagePayload] = Field(min_length=1, max_length=200)


class CheckConsistencyResponse(BaseModel):
    """Только подтверждённые межстраничные замечания."""

    findings: list[FindingDraftPayload]


def _options(container: ApplicationContainer) -> DocumentContextOptions:
    """Собирает typed лимиты из настроек сервиса."""
    settings = container.settings.document_context
    return DocumentContextOptions(
        max_related_facts_per_page=settings.max_related_facts_per_page,
        max_text_sources_per_page=settings.max_text_sources_per_page,
        semantic_threshold=settings.semantic_threshold,
        max_cross_page_candidates=settings.max_cross_page_candidates,
        max_evidence_sources_per_finding=settings.max_evidence_sources_per_finding,
        validation_batch_size=settings.validation_batch_size,
        validation_num_predict=settings.validation_num_predict,
    )


@router.post("/pages", response_model=BuildPageContextsResponse)
async def build_page_contexts(
    request: BuildPageContextsRequest,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> BuildPageContextsResponse:
    """Ищет exact и adjacent D-факты для каждого листа."""
    if not container.settings.document_context.enabled:
        return BuildPageContextsResponse(
            items=[
                PageContextPayload(page_number=page.page_number, sources=[])
                for page in request.pages
            ]
        )
    pages = tuple(page.to_domain() for page in request.pages)
    semantic = {item.page_number: tuple(item.sources) for item in request.semantic}
    sources = build_page_document_context(
        pages=pages,
        semantic_by_page=semantic,
        options=_options(container),
    )
    return BuildPageContextsResponse(
        items=[
            PageContextPayload(page_number=page.page, sources=list(sources[page.page]))
            for page in pages
        ]
    )


@router.post("/check-consistency", response_model=CheckConsistencyResponse)
async def check_cross_page_consistency(
    request: CheckConsistencyRequest,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> CheckConsistencyResponse:
    """Проверяет кандидатов без попарного VLM fan-out."""
    use_case = container.check_cross_page_consistency
    if use_case is None:
        return CheckConsistencyResponse(findings=[])
    probe = container.analysis_progress_probe

    async def cancelled() -> bool:
        """Проверяет durable отмену перед следующим пакетом VLM."""
        return bool(
            probe is not None
            and await probe.is_cancelled(
                document_id=request.document_id,
                stage="checking_cross_page_consistency",
                current=1,
                total=1,
            )
        )

    try:
        findings = await use_case.execute(
            pages=tuple(page.to_domain() for page in request.pages),
            cancel_check=cancelled,
        )
    except CrossPageCancelled as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "analysis_cancelled"},
        ) from error
    except VisionModelError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return CheckConsistencyResponse(
        findings=[
            FindingDraftPayload.model_validate(
                {
                    "finding_id": finding.finding_id,
                    "page": finding.page,
                    "page_type": finding.page_type,
                    "category": finding.category,
                    "severity": finding.severity,
                    "status": finding.status,
                    "comment": finding.comment,
                    "evidence": finding.evidence,
                    "recommendation_draft": finding.recommendation_draft,
                    "confidence": finding.confidence,
                    "normative_source_ids": [],
                    "basis": finding.basis,
                    "basis_sources": [],
                    "experience_query": finding.experience_query,
                    "visual_regions": [],
                    "evidence_locations": list(finding.evidence_locations),
                    "object_ref": finding.object_ref,
                }
            )
            for finding in findings
        ]
    )
