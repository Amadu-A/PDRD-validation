# services/analysis-service/src/pdrd_analysis_service/core/container.py

"""Composition root Analysis Service."""

from dataclasses import dataclass

from pdrd_analysis_service.application.ports.analysis_progress import (
    AnalysisProgressProbe,
)
from pdrd_analysis_service.application.use_cases import (
    AugmentProjectContext,
    BuildNormativeQueries,
    BuildProjectContextQuery,
    CheckPageAgainstNorms,
    CheckReadiness,
    FinalizeFindings,
    LocalizeFindings,
    UnderstandPage,
    ValidateProjectContext,
)
from pdrd_analysis_service.application.use_cases.technical_assignment_validation import (
    CheckPageAgainstTechnicalAssignment,
)
from pdrd_analysis_service.core.settings import (
    Settings,
    get_settings,
)
from pdrd_analysis_service.infrastructure.analysis_progress import (
    HttpAnalysisProgressProbe,
)
from pdrd_analysis_service.infrastructure.vllm import (
    VllmStructuredVisionModel,
)
from pdrd_analysis_service.infrastructure.vllm_cache import (
    CachedStructuredVisionModel,
)


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Runtime dependencies Analysis Service."""

    settings: Settings

    understand_page: UnderstandPage

    build_normative_queries: BuildNormativeQueries

    check_page_against_norms: CheckPageAgainstNorms

    check_page_against_technical_assignment: CheckPageAgainstTechnicalAssignment

    finalize_findings: FinalizeFindings

    localize_findings: LocalizeFindings

    check_readiness: CheckReadiness

    validate_project_context: ValidateProjectContext | None = None

    build_project_context_query: BuildProjectContextQuery | None = None

    augment_project_context: AugmentProjectContext | None = None

    analysis_progress_probe: AnalysisProgressProbe | None = None


def build_container() -> ApplicationContainer:
    """Собирает concrete runtime dependencies."""
    settings = get_settings()

    shared_vlm = VllmStructuredVisionModel(
        base_url=settings.vlm.base_url,
        model=settings.vlm.model,
        request_timeout_seconds=(settings.vlm.request_timeout_seconds),
        connect_timeout_seconds=(settings.vlm.connect_timeout_seconds),
        health_timeout_seconds=(settings.vlm.health_timeout_seconds),
        max_attempts=(settings.vlm.max_attempts),
        retry_backoff_seconds=(settings.vlm.retry_backoff_seconds),
        max_retry_num_predict=(settings.vlm.max_retry_num_predict),
    )

    vision_model = CachedStructuredVisionModel(
        delegate=shared_vlm,
        provider_identity=(f"{settings.vlm.base_url.rstrip('/')}|{settings.vlm.model}"),
        root_path=(settings.vlm.cache.root_path),
        namespace=(settings.vlm.cache.namespace),
        ttl_seconds=(settings.vlm.cache.ttl_seconds),
        enabled=(settings.vlm.cache.enabled),
    )

    progress_probe = HttpAnalysisProgressProbe(
        base_url=settings.progress.base_url,
        request_timeout_seconds=(settings.progress.request_timeout_seconds),
        connect_timeout_seconds=(settings.progress.connect_timeout_seconds),
    )

    return ApplicationContainer(
        settings=settings,
        understand_page=UnderstandPage(
            vision_model=vision_model,
            num_predict=(settings.pipeline.page_facts_num_predict),
        ),
        build_normative_queries=BuildNormativeQueries(
            max_queries=(settings.pipeline.max_normative_queries),
        ),
        check_page_against_norms=(
            CheckPageAgainstNorms(
                vision_model=vision_model,
                num_predict=(settings.pipeline.norm_check_num_predict),
                max_issues=(settings.pipeline.max_issues),
                normative_text_limit=(settings.pipeline.normative_text_limit),
            )
        ),
        check_page_against_technical_assignment=(
            CheckPageAgainstTechnicalAssignment(
                vision_model=vision_model,
                num_predict=(settings.pipeline.technical_assignment_num_predict),
                batch_size=(settings.pipeline.technical_assignment_batch_size),
                requirement_text_limit=(
                    settings.pipeline.technical_assignment_requirement_text_limit
                ),
            )
        ),
        finalize_findings=FinalizeFindings(
            vision_model=vision_model,
            num_predict=(settings.pipeline.final_num_predict),
            batch_size=(settings.pipeline.final_batch_size),
            experience_context_limit=(settings.pipeline.experience_context_limit),
            experience_min_score=(settings.pipeline.experience_min_score),
        ),
        localize_findings=LocalizeFindings(
            vision_model=vision_model,
            num_predict=(settings.pipeline.finding_location_num_predict),
        ),
        check_readiness=CheckReadiness(
            vision_model=vision_model,
        ),
        validate_project_context=(
            ValidateProjectContext(
                vision_model=vision_model,
                classify_batch_size=(settings.project_context.classify_batch_size),
                classify_num_predict=(settings.project_context.classify_num_predict),
                min_text_length=(settings.project_context.min_text_length),
                reject_confidence=(settings.project_context.reject_confidence),
            )
        ),
        build_project_context_query=(
            BuildProjectContextQuery(
                source_text_limit=(settings.project_context.query_source_text_limit),
            )
        ),
        augment_project_context=(
            AugmentProjectContext(
                context_text_limit=(settings.project_context.context_text_limit),
            )
        ),
        analysis_progress_probe=(progress_probe),
    )
