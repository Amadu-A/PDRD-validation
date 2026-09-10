# services/analysis-service/src/pdrd_analysis_service/transport/http/technical_assignment_routes.py

"""Internal HTTP API независимой T-first проверки."""

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
from pdrd_analysis_service.application.use_cases.technical_assignment_validation import (
    TechnicalAssignmentValidationError,
)
from pdrd_analysis_service.core.container import (
    ApplicationContainer,
)
from pdrd_analysis_service.domain.analysis import (
    FindingDraft,
)
from pdrd_analysis_service.transport.http.dependencies import (
    get_container,
)
from pdrd_analysis_service.transport.http.schemas import (
    FindingDraftPayload,
    NormativeSourcePayload,
    TechnicalAssignmentSourcePayload,
    UserPackageSourcePayload,
)
from pdrd_analysis_service.transport.http.technical_assignment_schemas import (
    CheckTechnicalAssignmentRequest,
    CheckTechnicalAssignmentResponse,
    TechnicalAssignmentDecisionPayload,
)

router = APIRouter()


def _validate_requirement_count(
    *,
    actual: int,
    max_allowed: int,
) -> None:
    """Не допускает неограниченный T-first request на один лист."""
    if actual <= max_allowed:
        return

    raise HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=(
            "Количество требований ТЗ для одного листа "
            f"({actual}) превышает configured limit "
            f"({max_allowed})."
        ),
    )


def _decode_image(
    *,
    encoded: str,
    max_bytes: int,
) -> bytes:
    """Декодирует image для independent T-first route."""
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
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=("image_base64 содержит некорректный Base64."),
        ) from error

    if not content:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail="Передано пустое изображение.",
        )

    if (
        len(
            content,
        )
        > max_bytes
    ):
        raise HTTPException(
            status_code=(status.HTTP_413_CONTENT_TOO_LARGE),
            detail=("Изображение превышает допустимый размер."),
        )

    return content


def _finding_payload(
    finding: FindingDraft,
) -> FindingDraftPayload:
    """Преобразует T-first finding в общий HTTP contract."""
    return FindingDraftPayload(
        finding_id=finding.finding_id,
        page=finding.page,
        page_type=finding.page_type,
        category=finding.category,
        severity=finding.severity,
        status=finding.status,
        comment=finding.comment,
        evidence=finding.evidence,
        recommendation_draft=(finding.recommendation_draft),
        confidence=finding.confidence,
        normative_source_ids=list(
            finding.normative_source_ids,
        ),
        basis=finding.basis,
        basis_sources=[
            NormativeSourcePayload(
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
            for source in finding.basis_sources
        ],
        experience_query=(finding.experience_query),
        technical_assignment_source_ids=list(
            finding.technical_assignment_source_ids,
        ),
        technical_assignment_basis_sources=[
            TechnicalAssignmentSourcePayload(
                source_id=source.source_id,
                point_id=source.point_id,
                score=source.score,
                technical_assignment_id=(source.technical_assignment_id),
                analysis_document_id=(source.analysis_document_id),
                section_id=source.section_id,
                source_sha256=source.source_sha256,
                source_file=source.source_file,
                page=source.page,
                text=source.text,
                normative_refs=list(
                    source.normative_refs,
                ),
            )
            for source in finding.technical_assignment_basis_sources
        ],
        user_package_source_ids=list(
            finding.user_package_source_ids,
        ),
        user_package_basis_sources=[
            UserPackageSourcePayload(
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
            for source in finding.user_package_basis_sources
        ],
    )


@router.post(
    "/internal/v1/pages/check-technical-assignment",
    response_model=CheckTechnicalAssignmentResponse,
)
async def check_technical_assignment(
    request: CheckTechnicalAssignmentRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> CheckTechnicalAssignmentResponse:
    """Выполняет exhaustive independent T-first check."""
    _validate_requirement_count(
        actual=len(
            request.requirements,
        ),
        max_allowed=(
            container.settings.pipeline.technical_assignment_max_requirements_per_page
        ),
    )

    image = _decode_image(
        encoded=request.image_base64,
        max_bytes=(container.settings.pipeline.max_image_bytes),
    )

    try:
        (
            summary,
            decisions,
            findings,
            metrics,
        ) = await container.check_page_against_technical_assignment.execute(
            page_number=request.page_number,
            page_type=(request.page_facts.page_type),
            extracted_text=(request.extracted_text),
            page_facts=(request.page_facts.to_domain()),
            image_bytes=image,
            technical_assignment_id=str(
                request.technical_assignment_id,
            ),
            analysis_document_id=str(
                request.analysis_document_id,
            ),
            section_id=str(
                request.section_id,
            ),
            source_file=request.source_file,
            source_sha256=(request.source_sha256),
            requirements=tuple(
                requirement.to_domain() for requirement in request.requirements
            ),
        )

    except ValueError as error:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(
                error,
            ),
        ) from error

    except (
        VisionModelError,
        TechnicalAssignmentValidationError,
    ) as error:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(
                error,
            ),
        ) from error

    return CheckTechnicalAssignmentResponse(
        summary=summary,
        decisions=[
            TechnicalAssignmentDecisionPayload(
                requirement_id=(decision.requirement_id),
                status=decision.status,
                severity=decision.severity,
                comment=decision.comment,
                evidence=decision.evidence,
                recommendation_draft=(decision.recommendation_draft),
                confidence=decision.confidence,
            )
            for decision in decisions
        ],
        findings=[
            _finding_payload(
                finding,
            )
            for finding in findings
        ],
        metrics=[metric.as_dict() for metric in metrics],
    )
