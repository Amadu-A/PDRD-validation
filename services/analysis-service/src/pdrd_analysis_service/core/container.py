# services/analysis-service/src/pdrd_analysis_service/core/container.py

"""Composition root Analysis Service."""

from dataclasses import dataclass

from pdrd_analysis_service.application.use_cases import (
    AugmentProjectContext,
    BuildNormativeQueries,
    BuildProjectContextQuery,
    CheckPageAgainstNorms,
    CheckReadiness,
    FinalizeFindings,
    UnderstandPage,
    ValidateProjectContext,
)
from pdrd_analysis_service.core.settings import (
    Settings,
    get_settings,
)
from pdrd_analysis_service.infrastructure.ollama import (
    OllamaStructuredVisionModel,
)


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Runtime dependencies Analysis Service."""

    settings: Settings

    understand_page: UnderstandPage

    build_normative_queries: BuildNormativeQueries

    check_page_against_norms: CheckPageAgainstNorms

    finalize_findings: FinalizeFindings

    check_readiness: CheckReadiness

    validate_project_context: ValidateProjectContext | None = None

    build_project_context_query: BuildProjectContextQuery | None = None

    augment_project_context: AugmentProjectContext | None = None


def build_container() -> ApplicationContainer:
    """Собирает concrete runtime dependencies."""
    settings = get_settings()

    vision_model = OllamaStructuredVisionModel(
        base_url=(settings.vision.base_url),
        model=(settings.vision.model),
        request_timeout_seconds=(settings.vision.request_timeout_seconds),
        connect_timeout_seconds=(settings.vision.connect_timeout_seconds),
        health_timeout_seconds=(settings.vision.health_timeout_seconds),
        num_ctx=(settings.vision.num_ctx),
        max_retries=(settings.vision.max_retries),
        keep_alive=(settings.vision.keep_alive),
        max_retry_num_predict=(settings.vision.max_retry_num_predict),
    )

    return ApplicationContainer(
        settings=settings,
        understand_page=UnderstandPage(
            vision_model=vision_model,
            num_predict=(settings.pipeline.page_facts_num_predict),
        ),
        build_normative_queries=(
            BuildNormativeQueries(
                max_queries=(settings.pipeline.max_normative_queries),
            )
        ),
        check_page_against_norms=(
            CheckPageAgainstNorms(
                vision_model=vision_model,
                num_predict=(settings.pipeline.norm_check_num_predict),
                max_issues=(settings.pipeline.max_issues),
                normative_text_limit=(settings.pipeline.normative_text_limit),
            )
        ),
        finalize_findings=FinalizeFindings(
            vision_model=vision_model,
            num_predict=(settings.pipeline.final_num_predict),
            batch_size=(settings.pipeline.final_batch_size),
            experience_context_limit=(settings.pipeline.experience_context_limit),
            experience_min_score=(settings.pipeline.experience_min_score),
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
    )
