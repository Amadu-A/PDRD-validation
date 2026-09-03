# services/analysis-service/src/pdrd_analysis_service/transport/http/routes.py

"""Internal HTTP API Analysis Service."""

import base64
import binascii
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelError,
)
from pdrd_analysis_service.core.container import (
    ApplicationContainer,
)
from pdrd_analysis_service.domain.analysis import (
    ExperienceSource,
    FinalFinding,
    FindingDraft,
    NormativeSource,
    PageFacts,
)
from pdrd_analysis_service.transport.http.dependencies import (
    get_container,
)
from pdrd_analysis_service.transport.http.schemas import (
    CheckNormsRequest,
    CheckNormsResponse,
    ExperienceSourcePayload,
    FinalFindingPayload,
    FinalizeRequest,
    FinalizeResponse,
    FindingDraftPayload,
    LiveHealthResponse,
    NormativeQueriesRequest,
    NormativeQueriesResponse,
    NormativeSourcePayload,
    PageFactsPayload,
    ReadyHealthResponse,
    UnderstandPageRequest,
    UnderstandPageResponse,
)

router = APIRouter()


def _decode_image(
    *,
    encoded: str,
    max_bytes: int,
) -> bytes:
    """Декодирует и проверяет base64 image."""
    try:
        content = base64.b64decode(
            encoded,
            validate=True,
        )

    except (
        binascii.Error,
        ValueError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="image_base64 содержит некорректный Base64.",
        ) from error

    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Передано пустое изображение.",
        )

    if (
        len(
            content,
        )
        > max_bytes
    ):
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Изображение превышает допустимый размер.",
        )

    return content


def _page_facts_payload(
    facts: PageFacts,
) -> PageFactsPayload:
    """Преобразует PageFacts в HTTP payload."""
    return PageFactsPayload(
        discipline=facts.discipline,
        page_type=facts.page_type,
        summary=facts.summary,
        objects=list(
            facts.objects,
        ),
        connections=list(
            facts.connections,
        ),
        labels=list(
            facts.labels,
        ),
        normative_queries=list(
            facts.normative_queries,
        ),
    )


def _normative_source_payload(
    source: NormativeSource,
) -> NormativeSourcePayload:
    """Преобразует managed normative source в HTTP payload."""
    return NormativeSourcePayload(
        source_id=source.source_id,
        point_id=source.point_id,
        score=source.score,
        document_id=source.document_id,
        section_id=source.section_id,
        category_id=source.category_id,
        source_sha256=source.source_sha256,
        source_file=source.source_file,
        source_path=source.source_path,
        page=source.page,
        chunk_index=source.chunk_index,
        text=source.text,
    )


def _experience_source_payload(
    source: ExperienceSource,
) -> ExperienceSourcePayload:
    """Преобразует ExperienceSource в HTTP payload."""
    return ExperienceSourcePayload(
        source_id=source.source_id,
        point_id=source.point_id,
        score=source.score,
        project_id=source.project_id,
        issue_id=source.issue_id,
        issue_text=source.issue_text,
        status=source.status,
        verified_fixed=source.verified_fixed,
        before_page=source.before_page,
        after_page=source.after_page,
        before_context=source.before_context,
        after_context=source.after_context,
    )


def _finding_draft_payload(
    finding: FindingDraft,
) -> FindingDraftPayload:
    """Преобразует FindingDraft в HTTP payload."""
    return FindingDraftPayload(
        finding_id=finding.finding_id,
        page=finding.page,
        page_type=finding.page_type,
        category=finding.category,
        severity=finding.severity,
        status=finding.status,
        comment=finding.comment,
        evidence=finding.evidence,
        recommendation_draft=finding.recommendation_draft,
        confidence=finding.confidence,
        normative_source_ids=list(
            finding.normative_source_ids,
        ),
        basis=finding.basis,
        basis_sources=[
            _normative_source_payload(
                source,
            )
            for source in finding.basis_sources
        ],
        experience_query=finding.experience_query,
    )


def _final_finding_payload(
    finding: FinalFinding,
) -> FinalFindingPayload:
    """Преобразует FinalFinding в HTTP payload."""
    return FinalFindingPayload(
        finding_id=finding.finding_id,
        page=finding.page,
        page_type=finding.page_type,
        category=finding.category,
        severity=finding.severity,
        status=finding.status,
        comment=finding.comment,
        evidence=finding.evidence,
        recommendation=finding.recommendation,
        confidence=finding.confidence,
        basis=finding.basis,
        basis_sources=[
            _normative_source_payload(
                source,
            )
            for source in finding.basis_sources
        ],
        experience_sources=[
            _experience_source_payload(
                source,
            )
            for source in finding.experience_sources
        ],
    )


@router.get(
    "/health/live",
    response_model=LiveHealthResponse,
)
async def health_live(
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> LiveHealthResponse:
    """Возвращает liveness."""
    return LiveHealthResponse(
        status="ok",
        service=container.settings.service_name,
        version=container.settings.service_version,
    )


@router.get(
    "/health/ready",
    response_model=ReadyHealthResponse,
)
async def health_ready(
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> ReadyHealthResponse:
    """Проверяет configured VLM."""
    report = await container.check_readiness.execute()

    dependencies = {
        "vision_model": report.vision_model,
    }

    if not report.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "not_ready",
                "dependencies": dependencies,
            },
        )

    return ReadyHealthResponse(
        status="ready",
        service=container.settings.service_name,
        version=container.settings.service_version,
        dependencies=dependencies,
    )


@router.post(
    "/internal/v1/pages/understand",
    response_model=UnderstandPageResponse,
)
async def understand_page(
    request: UnderstandPageRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> UnderstandPageResponse:
    """Получает объективные факты листа."""
    image = _decode_image(
        encoded=request.image_base64,
        max_bytes=container.settings.pipeline.max_image_bytes,
    )

    try:
        facts, metrics = await container.understand_page.execute(
            page_number=request.page_number,
            heuristic_page_type=request.heuristic_page_type,
            extracted_text=request.extracted_text,
            image_bytes=image,
        )

    except VisionModelError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(
                error,
            ),
        ) from error

    return UnderstandPageResponse(
        facts=_page_facts_payload(
            facts,
        ),
        metrics=metrics.as_dict(),
    )


@router.post(
    "/internal/v1/pages/normative-queries",
    response_model=NormativeQueriesResponse,
)
async def normative_queries(
    request: NormativeQueriesRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> NormativeQueriesResponse:
    """Строит retrieval queries Knowledge Service."""
    queries = container.build_normative_queries.execute(
        page_facts=request.page_facts.to_domain(),
        extracted_text=request.extracted_text,
        project_context_texts=tuple(
            request.project_context_texts,
        ),
    )

    return NormativeQueriesResponse(
        queries=list(
            queries,
        ),
    )


@router.post(
    "/internal/v1/pages/check-norms",
    response_model=CheckNormsResponse,
)
async def check_norms(
    request: CheckNormsRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> CheckNormsResponse:
    """Проверяет лист по retrieved normative sources."""
    image = _decode_image(
        encoded=request.image_base64,
        max_bytes=container.settings.pipeline.max_image_bytes,
    )

    try:
        (
            summary,
            findings,
            metrics,
        ) = await container.check_page_against_norms.execute(
            page_number=request.page_number,
            extracted_text=request.extracted_text,
            page_facts=request.page_facts.to_domain(),
            normative_sources=tuple(
                source.to_domain() for source in request.normative_sources
            ),
            image_bytes=image,
            normative_system_prompt=request.normative_system_prompt,
        )

    except VisionModelError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(
                error,
            ),
        ) from error

    return CheckNormsResponse(
        summary=summary,
        findings=[
            _finding_draft_payload(
                finding,
            )
            for finding in findings
        ],
        metrics=metrics.as_dict(),
    )


@router.post(
    "/internal/v1/findings/finalize",
    response_model=FinalizeResponse,
)
async def finalize_findings(
    request: FinalizeRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> FinalizeResponse:
    """Финализирует нормативные findings."""
    (
        summary,
        findings,
        metrics,
    ) = await container.finalize_findings.execute(
        findings=tuple(finding.to_domain() for finding in request.findings),
        experience_by_finding={
            finding_id: tuple(source.to_domain() for source in sources)
            for finding_id, sources in request.experience_by_finding.items()
        },
    )

    return FinalizeResponse(
        summary=summary,
        findings=[
            _final_finding_payload(
                finding,
            )
            for finding in findings
        ],
        metrics=metrics,
    )
