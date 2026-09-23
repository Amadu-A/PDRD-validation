# services/analysis-service/src/pdrd_analysis_service/application/use_cases/finalization.py

"""Use case финализации findings."""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from pdrd_analysis_service.application.json_schemas import (
    build_finalization_schema,
)
from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
    VisionModelError,
)
from pdrd_analysis_service.application.prompts import (
    build_finalization_prompt,
)
from pdrd_analysis_service.application.use_cases.common import (
    build_basis,
    string_tuple,
)
from pdrd_analysis_service.domain.analysis import (
    ExperienceSource,
    FinalFinding,
    FindingDraft,
    NormativeSource,
)

_NORMATIVE_REFERENCE_PATTERN = re.compile(
    r"\b(?:ГОСТ|GOST|ПУЭ|PUE|СНиП|SNIP|СП)\b",
    flags=re.IGNORECASE,
)

_SPLITTABLE_FINALIZATION_ERROR_MARKERS = (
    "Контекст Ollama исчерпан",
    "Ollama исчерпал output budget",
    "Модель не смогла сформировать корректный JSON",
)

logger = logging.getLogger(
    "uvicorn.error",
)


@dataclass(frozen=True, slots=True)
class _BatchOutcome:
    """Результат одного batch с возможным safe split."""

    findings: tuple[
        FinalFinding,
        ...,
    ]

    metrics: tuple[
        dict[str, Any],
        ...,
    ]

    fallback_count: int

    vlm_call_count: int
    successful_vlm_call_count: int
    failed_vlm_call_count: int

    split_count: int

    rejected_findings: tuple[
        dict[
            str,
            object,
        ],
        ...,
    ]


def _dedupe_normative_sources(
    sources: tuple[
        NormativeSource,
        ...,
    ],
) -> tuple[
    NormativeSource,
    ...,
]:
    """Дедуплицирует N-sources, сохраняя исходный порядок."""
    result: list[NormativeSource] = []

    seen: set[
        tuple[
            str,
            ...,
        ]
    ] = set()

    for source in sources:
        if source.point_id:
            key = (
                "point_id",
                source.point_id,
            )
        else:
            key = (
                "fallback",
                source.source_id,
                str(
                    source.document_id or "",
                ),
                str(
                    source.page or "",
                ),
                str(
                    source.chunk_index or "",
                ),
                source.text[:120],
            )

        if key in seen:
            continue

        seen.add(
            key,
        )

        result.append(
            source,
        )

    return tuple(
        result,
    )


def _available_normative_sources(
    *,
    finding: FindingDraft,
    normative_candidates: tuple[
        NormativeSource,
        ...,
    ],
) -> dict[
    str,
    NormativeSource,
]:
    """Строит разрешённый набор existing + finding-local N-sources."""
    result: dict[
        str,
        NormativeSource,
    ] = {}

    for source in (
        *finding.basis_sources,
        *normative_candidates,
    ):
        if not source.source_id:
            continue

        result.setdefault(
            source.source_id,
            source,
        )

    return result


def _fallback_recommendation(
    finding: FindingDraft,
) -> str:
    """Возвращает безопасную исходную рекомендацию."""
    recommendation = finding.recommendation_draft.strip()

    if recommendation:
        return recommendation

    return (
        "Проверить указанное несоответствие "
        "и при необходимости скорректировать "
        "проектное решение."
    )


def _safe_generated_text(
    *,
    generated: str,
    fallback: str,
    generic: str,
) -> str:
    """Не допускает неподтверждённые нормативные ссылки в свободный текст."""
    normalized_generated = generated.strip()

    if (
        normalized_generated
        and _NORMATIVE_REFERENCE_PATTERN.search(
            normalized_generated,
        )
        is None
    ):
        return normalized_generated

    normalized_fallback = fallback.strip()

    if (
        normalized_fallback
        and _NORMATIVE_REFERENCE_PATTERN.search(
            normalized_fallback,
        )
        is None
    ):
        return normalized_fallback

    return generic


def _candidate_groups(
    *,
    findings: tuple[
        FindingDraft,
        ...,
    ],
    normative_candidates_by_finding: dict[
        str,
        tuple[
            NormativeSource,
            ...,
        ],
    ],
    legacy_candidates: tuple[
        NormativeSource,
        ...,
    ],
) -> dict[
    str,
    tuple[
        NormativeSource,
        ...,
    ],
]:
    """Нормализует finding-local N candidates и legacy single-finding input."""
    result = {
        finding.finding_id: _dedupe_normative_sources(
            normative_candidates_by_finding.get(
                finding.finding_id,
                (),
            )
        )
        for finding in findings
    }

    if (
        len(
            findings,
        )
        == 1
        and legacy_candidates
        and not result[findings[0].finding_id]
    ):
        result[findings[0].finding_id] = _dedupe_normative_sources(
            legacy_candidates,
        )

    return result


def _normative_candidate_payload(
    source: NormativeSource,
    *,
    text_limit: int,
) -> dict[str, object]:
    """Сериализует finding-local N candidate для VLM prompt."""
    return {
        "source_id": source.source_id,
        "score": source.score,
        "document_id": source.document_id,
        "section_id": source.section_id,
        "source_sha256": source.source_sha256,
        "source_file": source.source_file,
        "page": source.page,
        "chunk_index": source.chunk_index,
        "text": source.text[:text_limit],
    }


def _finding_local_candidate_context(
    *,
    findings: tuple[
        FindingDraft,
        ...,
    ],
    candidate_groups: dict[
        str,
        tuple[
            NormativeSource,
            ...,
        ],
    ],
    text_limit: int,
) -> str:
    """Строит явное соответствие finding_id -> разрешённые N candidates."""
    payload = [
        {
            "finding_id": finding.finding_id,
            "normative_candidates": [
                _normative_candidate_payload(
                    source,
                    text_limit=text_limit,
                )
                for source in candidate_groups.get(
                    finding.finding_id,
                    (),
                )
            ],
        }
        for finding in findings
    ]

    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(
            ",",
            ":",
        ),
    )


def _allowed_normative_ids(
    *,
    findings: tuple[
        FindingDraft,
        ...,
    ],
    candidate_groups: dict[
        str,
        tuple[
            NormativeSource,
            ...,
        ],
    ],
) -> tuple[
    str,
    ...,
]:
    """Собирает union разрешённых N IDs для JSON Schema batch."""
    result: list[str] = []

    seen: set[str] = set()

    for finding in findings:
        for source in (
            *finding.basis_sources,
            *candidate_groups.get(
                finding.finding_id,
                (),
            ),
        ):
            source_id = source.source_id.strip()

            if not source_id or source_id in seen:
                continue

            seen.add(
                source_id,
            )

            result.append(
                source_id,
            )

    return tuple(
        result,
    )


def _can_split_finalization_error(
    error: VisionModelError,
) -> bool:
    """Разрешает split только для ошибок, зависящих от размера batch."""
    message = str(
        error,
    )

    return any(marker in message for marker in _SPLITTABLE_FINALIZATION_ERROR_MARKERS)


@dataclass(frozen=True, slots=True)
class FinalizeFindings:
    """Финализирует candidates, применяет conservative gate и N enrichment."""

    vision_model: StructuredVisionModel

    num_predict: int
    batch_size: int

    experience_context_limit: int
    experience_min_score: float

    async def execute(
        self,
        *,
        findings: tuple[
            FindingDraft,
            ...,
        ],
        experience_by_finding: dict[
            str,
            tuple[
                ExperienceSource,
                ...,
            ],
        ],
        normative_candidates: tuple[
            NormativeSource,
            ...,
        ] = (),
        normative_candidates_by_finding: dict[
            str,
            tuple[
                NormativeSource,
                ...,
            ],
        ]
        | None = None,
    ) -> tuple[
        str,
        tuple[
            FinalFinding,
            ...,
        ],
        dict[str, Any],
    ]:
        """Оформляет findings и удаляет только явно отклонённые candidates."""
        if self.batch_size < 1:
            raise ValueError(
                "finalization batch_size должен быть положительным.",
            )

        if not findings:
            return (
                "",
                (),
                {
                    "attempt": 0,
                    "done_reason": "no_findings",
                    "batch_size": self.batch_size,
                    "effective_batch_size": self.batch_size,
                    "initial_batch_count": 0,
                    "batch_execution_count": 0,
                    "fallback_count": 0,
                    "candidate_count": 0,
                    "kept_count": 0,
                    "rejected_count": 0,
                    "rejected_findings": [],
                    "vlm_call_count": 0,
                    "successful_vlm_call_count": 0,
                    "failed_vlm_call_count": 0,
                    "split_count": 0,
                    "batches": [],
                },
            )

        eligible_experience = {
            finding_id: tuple(
                source
                for source in sources
                if source.score >= self.experience_min_score
            )
            for finding_id, sources in experience_by_finding.items()
        }

        raw_candidate_groups = (
            normative_candidates_by_finding
            if normative_candidates_by_finding is not None
            else {}
        )

        isolated_enrichment = bool(
            raw_candidate_groups,
        )

        normalized_candidate_groups = _candidate_groups(
            findings=findings,
            normative_candidates_by_finding=raw_candidate_groups,
            legacy_candidates=normative_candidates,
        )

        legacy_candidates = (
            ()
            if isolated_enrichment
            else _dedupe_normative_sources(
                normative_candidates,
            )
        )

        effective_batch_size = self.batch_size

        initial_batch_count = (
            len(
                findings,
            )
            + effective_batch_size
            - 1
        ) // effective_batch_size

        final_items: list[FinalFinding] = []

        batch_metrics: list[
            dict[
                str,
                Any,
            ]
        ] = []

        fallback_count = 0

        rejected_findings: list[
            dict[
                str,
                object,
            ]
        ] = []

        vlm_call_count = 0
        successful_vlm_call_count = 0
        failed_vlm_call_count = 0

        split_count = 0

        for start in range(
            0,
            len(
                findings,
            ),
            effective_batch_size,
        ):
            batch = findings[start : start + effective_batch_size]

            outcome = await self._finalize_batch(
                batch=batch,
                eligible_experience=eligible_experience,
                normalized_candidate_groups=(normalized_candidate_groups),
                legacy_candidates=legacy_candidates,
                isolated_enrichment=isolated_enrichment,
                seed=300 + start,
                depth=0,
            )

            final_items.extend(
                outcome.findings,
            )

            batch_metrics.extend(
                outcome.metrics,
            )

            fallback_count += outcome.fallback_count

            rejected_findings.extend(
                outcome.rejected_findings,
            )

            vlm_call_count += outcome.vlm_call_count

            successful_vlm_call_count += outcome.successful_vlm_call_count

            failed_vlm_call_count += outcome.failed_vlm_call_count

            split_count += outcome.split_count

        if isolated_enrichment:
            normative_candidates_count = sum(
                len(
                    sources,
                )
                for sources in normalized_candidate_groups.values()
            )

            normative_candidate_groups_count = sum(
                bool(
                    sources,
                )
                for sources in normalized_candidate_groups.values()
            )
        else:
            normative_candidates_count = len(
                legacy_candidates,
            )

            normative_candidate_groups_count = int(
                bool(
                    legacy_candidates,
                )
            )

        logger.info(
            (
                "finalization_candidate_gate "
                "candidates=%s kept=%s rejected=%s "
                "fallback=%s rejected=%s"
            ),
            len(
                findings,
            ),
            len(
                final_items,
            ),
            len(
                rejected_findings,
            ),
            fallback_count,
            tuple(
                (
                    str(
                        item.get(
                            "finding_id",
                            "",
                        )
                    ),
                    str(
                        item.get(
                            "reason",
                            "",
                        )
                    )[:180],
                )
                for item in rejected_findings
            ),
        )

        return (
            "Замечания сформированы по результатам инженерной проверки.",
            tuple(
                final_items,
            ),
            {
                "attempt": 1,
                "done_reason": (
                    "completed_with_fallback" if fallback_count else "stop"
                ),
                "batch_size": self.batch_size,
                "effective_batch_size": effective_batch_size,
                "initial_batch_count": initial_batch_count,
                "batch_execution_count": len(
                    batch_metrics,
                ),
                "fallback_count": fallback_count,
                "candidate_count": len(
                    findings,
                ),
                "kept_count": len(
                    final_items,
                ),
                "rejected_count": len(
                    rejected_findings,
                ),
                "rejected_findings": rejected_findings,
                "experience_min_score": (self.experience_min_score),
                "isolated_normative_enrichment": (isolated_enrichment),
                "normative_candidates_count": (normative_candidates_count),
                "normative_candidate_groups_count": (normative_candidate_groups_count),
                "vlm_call_count": vlm_call_count,
                "successful_vlm_call_count": (successful_vlm_call_count),
                "failed_vlm_call_count": (failed_vlm_call_count),
                "split_count": split_count,
                "batches": batch_metrics,
            },
        )

    async def _finalize_batch(
        self,
        *,
        batch: tuple[
            FindingDraft,
            ...,
        ],
        eligible_experience: dict[
            str,
            tuple[
                ExperienceSource,
                ...,
            ],
        ],
        normalized_candidate_groups: dict[
            str,
            tuple[
                NormativeSource,
                ...,
            ],
        ],
        legacy_candidates: tuple[
            NormativeSource,
            ...,
        ],
        isolated_enrichment: bool,
        seed: int,
        depth: int,
    ) -> _BatchOutcome:
        """Финализирует batch и безопасно делит его при size-related ошибке."""
        finding_ids = tuple(finding.finding_id for finding in batch)

        candidate_groups = {
            finding.finding_id: (
                normalized_candidate_groups.get(
                    finding.finding_id,
                    (),
                )
                if isolated_enrichment
                else legacy_candidates
            )
            for finding in batch
        }

        allowed_normative_ids = _allowed_normative_ids(
            findings=batch,
            candidate_groups=candidate_groups,
        )

        try:
            prompt = build_finalization_prompt(
                findings=batch,
                experience_by_finding=eligible_experience,
                experience_context_limit=(self.experience_context_limit),
                normative_candidates=(() if isolated_enrichment else legacy_candidates),
            )

            if isolated_enrichment:
                local_candidates_json = _finding_local_candidate_context(
                    findings=batch,
                    candidate_groups=candidate_groups,
                    text_limit=(self.experience_context_limit),
                )

                prompt += f"""

FINDING-LOCAL NORMATIVE CANDIDATES:
{local_candidates_json}

КРИТИЧЕСКИ ВАЖНО ДЛЯ FINDING-LOCAL ENRICHMENT:

- в этом batch обрабатывается несколько независимых findings;

- массив normative_candidates внутри каждого объекта
  FINDING-LOCAL NORMATIVE CANDIDATES относится ТОЛЬКО
  к finding_id этого же объекта;

- N-source одного finding запрещено использовать
  для другого finding;

- top-level NORMATIVE CANDIDATES из основной DATA
  для этого режима намеренно пуст;

- существующие normative_basis внутри finding
  также относятся только к этому finding;

- названия и номера ГОСТ, СП, ПУЭ, СНиП
  НЕ пиши в comment и recommendation;

- подтверждённый норматив показывается пользователю
  отдельно через normative_source_ids,
  basis и basis_sources;

- comment описывает проблему,
  recommendation описывает действие;

- если подходящего N нет,
  candidate всё равно обязательно возвращается
  как JSON item;

- отсутствие N само по себе НЕ является
  причиной decision=reject;

- decision=reject допустим только по правилам
  candidate quality gate из основного prompt.
""".rstrip()

            generation = await self.vision_model.generate_json(
                prompt=prompt,
                schema=build_finalization_schema(
                    finding_ids,
                    normative_source_ids=(allowed_normative_ids),
                ),
                num_predict=self.num_predict,
                seed=seed,
                stage=(
                    "finalization:"
                    + ",".join(
                        finding_ids,
                    )
                ),
                image_bytes=None,
            )

        except VisionModelError as error:
            if len(
                batch,
            ) > 1 and _can_split_finalization_error(
                error,
            ):
                split_at = (
                    len(
                        batch,
                    )
                    // 2
                )

                left = await self._finalize_batch(
                    batch=batch[:split_at],
                    eligible_experience=eligible_experience,
                    normalized_candidate_groups=(normalized_candidate_groups),
                    legacy_candidates=legacy_candidates,
                    isolated_enrichment=(isolated_enrichment),
                    seed=seed + 1,
                    depth=depth + 1,
                )

                right = await self._finalize_batch(
                    batch=batch[split_at:],
                    eligible_experience=eligible_experience,
                    normalized_candidate_groups=(normalized_candidate_groups),
                    legacy_candidates=legacy_candidates,
                    isolated_enrichment=(isolated_enrichment),
                    seed=seed + 2,
                    depth=depth + 1,
                )

                return _BatchOutcome(
                    findings=(
                        *left.findings,
                        *right.findings,
                    ),
                    metrics=(
                        {
                            "finding_ids": list(
                                finding_ids,
                            ),
                            "batch_depth": depth,
                            "batch_size": len(
                                batch,
                            ),
                            "outcome": "split_retry",
                            "fallback": False,
                            "fallback_count": 0,
                            "error": str(
                                error,
                            )[:1200],
                        },
                        *left.metrics,
                        *right.metrics,
                    ),
                    fallback_count=(left.fallback_count + right.fallback_count),
                    vlm_call_count=(1 + left.vlm_call_count + right.vlm_call_count),
                    successful_vlm_call_count=(
                        left.successful_vlm_call_count + right.successful_vlm_call_count
                    ),
                    failed_vlm_call_count=(
                        1 + left.failed_vlm_call_count + right.failed_vlm_call_count
                    ),
                    split_count=(1 + left.split_count + right.split_count),
                    rejected_findings=(
                        *left.rejected_findings,
                        *right.rejected_findings,
                    ),
                )

            fallback_findings = tuple(
                self._fallback(
                    finding,
                )
                for finding in batch
            )

            return _BatchOutcome(
                findings=fallback_findings,
                metrics=(
                    {
                        "finding_ids": list(
                            finding_ids,
                        ),
                        "batch_depth": depth,
                        "batch_size": len(
                            batch,
                        ),
                        "outcome": "fallback",
                        "fallback": True,
                        "fallback_count": len(
                            batch,
                        ),
                        "error": str(
                            error,
                        )[:1200],
                    },
                ),
                fallback_count=len(
                    batch,
                ),
                vlm_call_count=1,
                successful_vlm_call_count=0,
                failed_vlm_call_count=1,
                split_count=0,
                rejected_findings=(),
            )

        returned = {
            str(
                item.get(
                    "finding_id",
                    "",
                )
            ): item
            for item in generation.payload.get(
                "findings",
                [],
            )
            if isinstance(
                item,
                dict,
            )
        }

        final_items: list[FinalFinding] = []

        batch_fallback_count = 0

        batch_rejected_findings: list[
            dict[
                str,
                object,
            ]
        ] = []

        for finding in batch:
            item = returned.get(
                finding.finding_id,
            )

            if item is None:
                final_items.append(
                    self._fallback(
                        finding,
                    )
                )

                batch_fallback_count += 1

                continue

            decision = (
                str(
                    item.get(
                        "decision",
                        "keep",
                    )
                )
                .strip()
                .casefold()
            )

            rejection_reason = str(
                item.get(
                    "rejection_reason",
                    "",
                )
            ).strip()

            if decision == "reject" and rejection_reason:
                batch_rejected_findings.append(
                    {
                        "finding_id": finding.finding_id,
                        "page": finding.page,
                        "reason": rejection_reason,
                    }
                )

                continue

            final_items.append(
                self._build_final(
                    finding=finding,
                    item=item,
                    available_experience=(
                        eligible_experience.get(
                            finding.finding_id,
                            (),
                        )
                    ),
                    normative_candidates=(
                        candidate_groups.get(
                            finding.finding_id,
                            (),
                        )
                    ),
                    guard_normative_free_text=(isolated_enrichment),
                )
            )

        return _BatchOutcome(
            findings=tuple(
                final_items,
            ),
            metrics=(
                {
                    "finding_ids": list(
                        finding_ids,
                    ),
                    "batch_depth": depth,
                    "batch_size": len(
                        batch,
                    ),
                    "outcome": "success",
                    "fallback": (batch_fallback_count > 0),
                    "fallback_count": (batch_fallback_count),
                    "rejected_count": len(
                        batch_rejected_findings,
                    ),
                    "rejected_finding_ids": [
                        str(
                            item["finding_id"],
                        )
                        for item in batch_rejected_findings
                    ],
                    **generation.metrics.as_dict(),
                },
            ),
            fallback_count=batch_fallback_count,
            vlm_call_count=1,
            successful_vlm_call_count=1,
            failed_vlm_call_count=0,
            split_count=0,
            rejected_findings=tuple(
                batch_rejected_findings,
            ),
        )

    @staticmethod
    def _fallback(
        finding: FindingDraft,
    ) -> FinalFinding:
        """Возвращает исходный finding без потери evidence."""
        return FinalFinding(
            finding_id=finding.finding_id,
            page=finding.page,
            page_type=finding.page_type,
            category=finding.category,
            severity=finding.severity,
            status=finding.status,
            comment=finding.comment,
            evidence=finding.evidence,
            recommendation=(
                _fallback_recommendation(
                    finding,
                )
            ),
            confidence=finding.confidence,
            basis=finding.basis,
            basis_sources=finding.basis_sources,
            experience_sources=(),
            technical_assignment_basis_sources=(
                finding.technical_assignment_basis_sources
            ),
            user_package_basis_sources=(finding.user_package_basis_sources),
        )

    @staticmethod
    def _build_final(
        *,
        finding: FindingDraft,
        item: dict[str, Any],
        available_experience: tuple[
            ExperienceSource,
            ...,
        ],
        normative_candidates: tuple[
            NormativeSource,
            ...,
        ],
        guard_normative_free_text: bool,
    ) -> FinalFinding:
        """Собирает final finding и валидирует finding-local N/E IDs."""
        experience_by_id = {source.source_id: source for source in available_experience}

        requested_experience_ids = string_tuple(
            item.get(
                "experience_source_ids",
            ),
            limit=2,
        )

        selected_experience = tuple(
            experience_by_id[source_id]
            for source_id in requested_experience_ids
            if source_id in experience_by_id
        )

        available_normative = _available_normative_sources(
            finding=finding,
            normative_candidates=(normative_candidates),
        )

        if "normative_source_ids" in item:
            requested_normative_ids = string_tuple(
                item.get(
                    "normative_source_ids",
                ),
                limit=3,
            )

            selected_normative = _dedupe_normative_sources(
                tuple(
                    available_normative[source_id]
                    for source_id in requested_normative_ids
                    if source_id in available_normative
                )
            )
        else:
            # Backward-compatible fallback для старых fake responses
            # и возможного in-flight legacy ответа.
            selected_normative = finding.basis_sources

        comment = str(
            item.get(
                "comment",
                "",
            )
        ).strip()

        recommendation = str(
            item.get(
                "recommendation",
                "",
            )
        ).strip()

        if guard_normative_free_text:
            comment = _safe_generated_text(
                generated=comment,
                fallback=finding.comment,
                generic=(
                    "На листе выявлено несоответствие, требующее проверки инженером."
                ),
            )

            recommendation = _safe_generated_text(
                generated=recommendation,
                fallback=_fallback_recommendation(
                    finding,
                ),
                generic=(
                    "Проверить указанное несоответствие "
                    "и при необходимости скорректировать "
                    "проектное решение."
                ),
            )
        else:
            if not comment:
                comment = finding.comment

            if not recommendation:
                recommendation = _fallback_recommendation(
                    finding,
                )

        finding_category = finding.category

        normalized_status = finding.status

        has_customer_basis = bool(
            finding.technical_assignment_basis_sources
            or finding.user_package_basis_sources
        )

        if not selected_normative:
            if finding_category == "normative_control":
                finding_category = (
                    "customer_requirements" if has_customer_basis else "other"
                )

            if not has_customer_basis:
                normalized_status = "needs_review"

        return FinalFinding(
            finding_id=finding.finding_id,
            page=finding.page,
            page_type=finding.page_type,
            category=finding_category,
            severity=finding.severity,
            status=normalized_status,
            comment=comment,
            evidence=finding.evidence,
            recommendation=recommendation,
            confidence=finding.confidence,
            basis=build_basis(
                selected_normative,
            ),
            basis_sources=selected_normative,
            experience_sources=selected_experience,
            technical_assignment_basis_sources=(
                finding.technical_assignment_basis_sources
            ),
            user_package_basis_sources=(finding.user_package_basis_sources),
        )
