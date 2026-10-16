# services/analysis-service/src/pdrd_analysis_service/application/use_cases/finalization.py

"""Финализация инженерных замечаний с проверкой доказательств N/T/U."""

import json
import logging
import re
from dataclasses import asdict, dataclass, replace
from typing import Any

from pdrd_analysis_service.application.candidate_trace import trace_candidate
from pdrd_analysis_service.application.engineering_program import (
    equipment_review_question,
    finalize_arithmetic_candidate,
    protected_equipment_question,
    protected_table_question,
    verified_equipment_check,
    verified_programmatic_check,
)
from pdrd_analysis_service.application.evidence_audit import (
    diagnostic_record,
    evidence_audit,
    finalize_evidence_candidate,
    protected_evidence_question,
)
from pdrd_analysis_service.application.json_schemas import (
    build_finalization_schema,
)
from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
    VisionModelError,
    VisionModelOutputBudgetError,
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
from pdrd_analysis_service.domain.engineering_assertion import (
    deterministic_unit_mismatch,
    read_assertion,
)
from pdrd_analysis_service.domain.engineering_claims import (
    REVIEW_REASON_CODES,
    false_numeric_claim,
    unsupported_engineering_claim,
)
from pdrd_analysis_service.domain.evidence_audit import diagnostic_language

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

    if recommendation and false_numeric_claim(recommendation) is None:
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

    reference = re.compile(
        r"\b(?:ГОСТ(?:\s+Р)?|GOST(?:\s+R)?|СП|SP|СНиП|SNIP|ПУЭ|PUE)\s+\d[\d.\-/]*", re.I
    )
    # Упоминание СП без номера и дословная ссылка самого проекта не являются
    # придуманным нормативным основанием. Проверяем новые обозначения отдельно.
    allowed = {match.casefold() for match in reference.findall(fallback)}
    invented = {
        match.casefold() for match in reference.findall(normalized_generated)
    } - allowed
    if normalized_generated and not invented:
        return normalized_generated

    normalized_fallback = fallback.strip()

    if normalized_fallback and (
        not reference.search(normalized_fallback)
        or "«" in normalized_fallback
        or re.search(r"указан|ссылк|назван|упомянут", normalized_fallback, re.I)
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
    if isinstance(error, VisionModelOutputBudgetError):
        return True
    message = str(
        error,
    )

    return any(marker in message for marker in _SPLITTABLE_FINALIZATION_ERROR_MARKERS)


def _is_protected_duplicate_review(finding: FindingDraft) -> bool:
    """Пропускает через factual-review только подтверждённую текстом запись.

    Обход VLM-финализатора касается факта повторения подписи, а не
    квалификации нарушения. Исходный N/T/U-кандидат остаётся на обычном пути.
    """
    match = re.fullmatch(
        r"p([1-9]\d*)-dpos-(\d+(?:-\d+){2,4})",
        finding.finding_id,
    )
    if match is None or int(match.group(1)) != finding.page:
        return False
    if finding.status != "needs_review" or any(
        (
            finding.basis_sources,
            finding.technical_assignment_basis_sources,
            finding.user_package_basis_sources,
        )
    ):
        return False
    tag = match.group(2).replace("-", ".")
    exact_tag = re.compile(r"(?<![\w.])" + re.escape(tag) + r"(?![\w]|\.\d)")
    combined_text = " ".join((finding.comment, finding.evidence)).casefold()
    return (
        exact_tag.search(finding.comment) is not None
        and exact_tag.search(finding.evidence) is not None
        and "повтор" in combined_text
        and "текст" in finding.evidence.casefold()
    )


def _is_protected_factual_review(finding: FindingDraft) -> bool:
    """Сохраняет конкретное подозрение с проверенным исходным evidence, не объявляя его нарушением."""
    if protected_evidence_question(finding):
        return True
    if protected_table_question(finding) or verified_programmatic_check(finding):
        return True
    if verified_equipment_check(finding) or protected_equipment_question(finding):
        return True
    if _is_protected_duplicate_review(finding):
        return True
    assertion = read_assertion(finding.origin_assertions)
    if assertion is not None and assertion.claim_type == "contradiction":
        if (
            assertion.verification == "deterministic"
            and assertion.verification_rule == "unit_equivalence"
            and all(
                item.fact.extraction_method in {"pdf_text", "pdf_table"}
                for item in assertion.observations
            )
            and deterministic_unit_mismatch(assertion)
        ):
            return True
        if (
            assertion.verification == "semantic"
            and assertion.verification_rule == "cross_page_comparability"
            and any(
                origin.get("origin") == "cross_page_validation"
                and origin.get("comparability_checked") is True
                for origin in finding.origin_assertions
            )
        ):
            return True
    return (
        finding.status == "needs_review"
        and bool(finding.comment.strip() and finding.evidence.strip())
        and not finding.basis_sources
        and not finding.technical_assignment_basis_sources
        and not finding.user_package_basis_sources
        and false_numeric_claim(finding.comment) is None
        and any(
            item.get("origin") == "engineering_verification"
            and item.get("evidence_grounded") is True
            and item.get("conclusion_verified") is True
            and item.get("verification_rule")
            in {"unit_equivalence", "explicit_system_purpose"}
            for item in finding.origin_assertions
        )
    )


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

        candidate_count = len(findings)
        pre_rejected = []
        accepted = []
        for finding in findings:
            finding = finalize_arithmetic_candidate(finding)
            finding = equipment_review_question(finding)
            finding = finalize_evidence_candidate(finding)
            diagnostic = diagnostic_record(finding)
            if diagnostic is not None:
                pre_rejected.append(diagnostic)
                continue
            reason = (
                "invalid_programmatic_proof"
                if any(
                    origin.get("origin") == "programmatic_engineering_check"
                    for origin in finding.origin_assertions
                )
                and not verified_programmatic_check(finding)
                else None
                if finding.status == "hypothesis"
                and any(
                    origin.get("review_kind") == "table_arithmetic"
                    for origin in finding.origin_assertions
                )
                else unsupported_engineering_claim(finding.comment)
            )
            if reason is not None:
                pre_rejected.append(
                    {
                        "finding_id": finding.finding_id,
                        "page": finding.page,
                        "reason": reason,
                    }
                )
                trace_candidate(
                    "finalization",
                    finding.finding_id,
                    page=finding.page,
                    decision="reject",
                    reason=reason,
                )
            else:
                accepted.append(finding)
        findings = tuple(accepted)

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
                    "deterministic_review_count": 0,
                    "candidate_count": candidate_count,
                    "kept_count": 0,
                    "rejected_count": len(pre_rejected),
                    "rejected_findings": pre_rejected,
                    "vlm_call_count": 0,
                    "successful_vlm_call_count": 0,
                    "failed_vlm_call_count": 0,
                    "split_count": 0,
                    "batches": [],
                },
            )

        # Проверенная текстом запись остаётся осторожным фактическим
        # замечанием; все прочие кандидаты проходят неизменный quality gate.
        protected = tuple(
            finding for finding in findings if _is_protected_factual_review(finding)
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
        # Вопрос не требует редактора, но уже найденные N/E нельзя потерять при обходе модели.
        # Сохраняем их как непроверенные подсказки, не назначая нормативным основанием.
        retrieved_candidates = {
            finding.finding_id: raw_candidate_groups.get(finding.finding_id)
            or (normative_candidates if len(findings) == 1 else ())
            for finding in findings
        }
        findings = tuple(
            replace(
                finding,
                origin_assertions=(
                    *finding.origin_assertions,
                    {
                        "origin": "unverified_enrichment_context",
                        "normative_candidates": [
                            asdict(source)
                            for source in retrieved_candidates[finding.finding_id]
                        ],
                        "experience_candidates": [
                            asdict(source)
                            for source in experience_by_finding.get(
                                finding.finding_id, ()
                            )
                        ],
                    },
                ),
            )
            if protected_evidence_question(finding)
            and (
                retrieved_candidates[finding.finding_id]
                or experience_by_finding.get(finding.finding_id)
            )
            else finding
            for finding in findings
        )

        # Проверенные текстом повторы проходят модель только ради finding-local
        # нормативного обоснования. Без найденных кандидатов их путь остаётся
        # полностью детерминированным.
        ordinary = tuple(
            finding
            for finding in findings
            if finding.status != "hypothesis"
            and not protected_evidence_question(finding)
            and not protected_table_question(finding)
            and not verified_programmatic_check(finding)
            and not verified_equipment_check(finding)
            and not protected_equipment_question(finding)
            and (
                (not _is_protected_factual_review(finding))
                or normalized_candidate_groups.get(finding.finding_id)
            )
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
                ordinary,
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
        ] = list(pre_rejected)

        vlm_call_count = 0
        successful_vlm_call_count = 0
        failed_vlm_call_count = 0

        split_count = 0

        for start in range(
            0,
            len(
                ordinary,
            ),
            effective_batch_size,
        ):
            batch = ordinary[start : start + effective_batch_size]

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

        # Стабильный порядок исходных findings сохраняется независимо от того,
        # на каком этапе конкретная фактическая запись была сформирована.
        ordinary_by_id = {item.finding_id: item for item in final_items}
        final_items = [
            ordinary_by_id.get(finding.finding_id, self._fallback(finding))
            for finding in findings
            if _is_protected_factual_review(finding)
            or finding.status == "hypothesis"
            or finding.finding_id in ordinary_by_id
        ]
        if protected:
            logger.info(
                "finalization_deterministic_review kept=%s ids=%s",
                len(protected),
                tuple(item.finding_id for item in protected),
            )
        kept_ids = {item.finding_id for item in final_items}
        reasons_by_id = {
            item["finding_id"]: item.get("reason_code", "model_reject")
            for item in rejected_findings
        }
        for finding in findings:
            decision = "keep" if finding.finding_id in kept_ids else "reject"
            for origin in finding.origin_assertions or ({},):
                trace_candidate(
                    "finalization",
                    str(origin.get("candidate_id") or finding.finding_id),
                    page=finding.page,
                    decision=decision,
                    reason="semantic_review"
                    if decision == "keep"
                    else str(reasons_by_id.get(finding.finding_id, "model_reject")),
                    finding_id=finding.finding_id,
                    locations=len(finding.evidence_locations),
                    sources=len(finding.basis_sources)
                    + len(finding.technical_assignment_basis_sources)
                    + len(finding.user_package_basis_sources)
                    + len(finding.document_context_basis_sources),
                )

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
                "deterministic_review_count": len(protected),
                "candidate_count": candidate_count,
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
            if generation.metrics.done_reason == "length":
                raise VisionModelOutputBudgetError(
                    "Финализация достигла предела вывода.", metrics=generation.metrics
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
                            **(
                                error.metrics.as_dict()
                                if isinstance(error, VisionModelOutputBudgetError)
                                and error.metrics
                                else {}
                            ),
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

            if isinstance(error, VisionModelOutputBudgetError):
                raise
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

            if _is_protected_factual_review(finding) and decision != "keep":
                final_items.append(self._fallback(finding))
                batch_fallback_count += 1
                continue

            if decision == "reject" and rejection_reason:
                batch_rejected_findings.append(
                    {
                        "finding_id": finding.finding_id,
                        "page": finding.page,
                        "reason": rejection_reason,
                        **(
                            {"reason_code": item["reason_code"]}
                            if item.get("reason_code") in REVIEW_REASON_CODES
                            else {}
                        ),
                    }
                )

                continue

            finalized = self._build_final(
                finding=finding,
                item=item,
                available_experience=(eligible_experience.get(finding.finding_id, ())),
                normative_candidates=(candidate_groups.get(finding.finding_id, ())),
                guard_normative_free_text=isolated_enrichment,
            )
            if _is_protected_factual_review(finding):
                finalized = replace(
                    finalized,
                    category=finding.category,
                    status=finding.status,
                    comment=finding.comment,
                    recommendation=_fallback_recommendation(finding),
                )
            final_items.append(finalized)

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
        assertion = read_assertion(finding.origin_assertions)
        return FinalFinding(
            finding_id=finding.finding_id,
            page=finding.page,
            page_type=finding.page_type,
            category=finding.category,
            severity=finding.severity,
            status="needs_review"
            if finding.status != "hypothesis"
            and (
                (assertion is not None and assertion.verification == "unverified")
                or (assertion is None and finding.category == "document_consistency")
            )
            else finding.status,
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
            origin_assertions=finding.origin_assertions,
            object_ref=finding.object_ref,
            evidence_locations=finding.evidence_locations,
            document_context_source_ids=finding.document_context_source_ids,
            document_context_basis_sources=finding.document_context_basis_sources,
            equipment_documentation_source_ids=finding.equipment_documentation_source_ids,
            equipment_documentation_basis_sources=finding.equipment_documentation_basis_sources,
            equipment_details=finding.equipment_details,
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
        if unsupported_engineering_claim(comment) is not None or diagnostic_language(
            comment
        ):
            # Редактор не имеет права заменить корректного кандидата ложным вычислением.
            comment = finding.comment
            recommendation = _fallback_recommendation(finding)
        if false_numeric_claim(recommendation) is not None or diagnostic_language(
            recommendation
        ):
            recommendation = _fallback_recommendation(finding)

        has_customer_basis = bool(
            finding.technical_assignment_basis_sources
            or finding.user_package_basis_sources
        )

        if not selected_normative:
            if finding_category == "normative_control":
                finding_category = (
                    "customer_requirements" if has_customer_basis else "other"
                )

            if not has_customer_basis and finding.category != "document_consistency":
                normalized_status = "needs_review"

        if _is_protected_factual_review(finding):
            comment = finding.comment
            recommendation = _fallback_recommendation(finding)

        origins = finding.origin_assertions
        assertion = read_assertion(origins)
        if assertion is None and finding.category == "document_consistency":
            normalized_status = "needs_review"
        audit = evidence_audit(finding)
        if audit is not None:
            # Редактор не может подменить проверенный предмет новой интерпретацией.
            comment = finding.comment
            recommendation = _fallback_recommendation(finding)
            if audit["disposition"] != "supported":
                normalized_status = "needs_review"
        if assertion is not None:
            if assertion.claim_type == "missing_justification":
                normalized_status = "needs_review"
            if (
                assertion.verification == "unverified"
                and audit is not None
                and audit["status"] == "supported"
            ):
                assertion = replace(
                    assertion,
                    verification="semantic",
                    verification_rule="final_comparability",
                )
                origins = (
                    assertion.as_origin(),
                    *(
                        {**origin, "origin": "engineering_assertion_history"}
                        for origin in origins
                        if origin.get("origin") == "engineering_assertion"
                    ),
                    *(
                        origin
                        for origin in origins
                        if origin.get("origin") != "engineering_assertion"
                    ),
                )

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
            origin_assertions=origins,
            object_ref=finding.object_ref,
            evidence_locations=finding.evidence_locations,
            document_context_source_ids=finding.document_context_source_ids,
            document_context_basis_sources=finding.document_context_basis_sources,
            equipment_documentation_source_ids=finding.equipment_documentation_source_ids,
            equipment_documentation_basis_sources=finding.equipment_documentation_basis_sources,
            equipment_details=finding.equipment_details,
        )
