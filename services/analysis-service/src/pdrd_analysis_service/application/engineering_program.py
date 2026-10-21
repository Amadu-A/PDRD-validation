# services/analysis-service/src/pdrd_analysis_service/application/engineering_program.py

"""Исполняемая инженерная программа поверх фактов и уже полученных источников."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from pdrd_analysis_service.application.evidence_audit import audit_findings_evidence
from pdrd_analysis_service.application.vision_execution import current_vision_execution
from pdrd_analysis_service.domain.analysis import (
    DocumentContextSource,
    FindingDraft,
    FindingVisualRegion,
)
from pdrd_analysis_service.domain.calculation_tables import column_unit_key
from pdrd_analysis_service.domain.design_decisions import (
    DecisionRegistry,
    build_decision_registry,
    stable_id,
)
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    document_fact_from_mapping,
    fact_search_text,
    normalize_label,
    scope_key,
)
from pdrd_analysis_service.domain.engineering_assertion import (
    EngineeringAssertion,
    read_assertion,
)
from pdrd_analysis_service.domain.engineering_calculations import scalar
from pdrd_analysis_service.domain.engineering_checks import (
    EngineeringCheck,
    evaluate_decisions,
    program_evidence_eligible,
)
from pdrd_analysis_service.domain.requirement_applicability import (
    RequirementEvidence,
    check_requirement,
)
from pdrd_analysis_service.domain.requirement_plan import (
    build_requirement_plan,
    requirement_checks,
    requirement_coverage,
    requirement_currency,
)
from pdrd_analysis_service.domain.signal_connections import check_signal_connections


@dataclass(frozen=True, slots=True)
class EngineeringReport:
    """Результат всех программных проверок, включая ограничения и отсутствие претензий."""

    registry: DecisionRegistry
    checks: tuple[EngineeringCheck, ...]
    requirement_plan: dict | None = None

    def as_dict(self):
        """Воспроизводит полный протокол без повторного VLM-чтения."""
        return {
            **self.registry.as_dict(),
            "checks": [check.as_dict() for check in self.checks],
            "new_vlm_calls": 0,
            **(
                {"requirement_plan": self.requirement_plan}
                if self.requirement_plan is not None
                else {}
            ),
        }


def run_engineering_program(
    registry: DecisionRegistry,
    *,
    page_texts: dict[int, str],
    requirement_sources=(),
    requirement_plan_enabled: bool = False,
    assessed_on: str | None = None,
) -> EngineeringReport:
    """Не выполняет retrieval или генерацию; источники уже принадлежат текущему сценарию."""
    requirement_sources = tuple(requirement_sources)
    assessed_on = assessed_on or datetime.now(UTC).date().isoformat()
    checks = list(evaluate_decisions(registry, page_texts=page_texts))
    checks.extend(check_signal_connections(registry))
    plan = None
    records = ()
    if requirement_plan_enabled:
        records = build_requirement_plan(
            tuple(item.observation for item in registry.evidence),
            page_texts=page_texts,
            sources=tuple(requirement_sources),
            assessed_on=assessed_on,
        )
        checks.extend(requirement_checks(records))
        plan = {
            "checks": [record.as_dict() for record in records],
            "coverage": requirement_coverage(records),
            "source_counts": {
                kind: sum(source.kind == kind for source in requirement_sources)
                for kind in ("N", "T", "U")
            },
        }
    legacy_outcomes = Counter()
    for evidence in registry.evidence:
        if not program_evidence_eligible(evidence):
            continue
        item = evidence.observation
        for source in requirement_sources:
            # Не размножаем пустые сравнения всех строк со всеми нормами.
            if (
                item.fact.identifier
                and item.fact.identifier.casefold() in source.text.casefold()
                and item.fact.property_name.casefold() in source.text.casefold()
            ):
                check = check_requirement(item, source)
                covered_conflict = any(
                    record.result == "source_conflict"
                    and source.source_id in record.source_ids
                    and record.object_key == check.object_key
                    and record.property_key == check.property_key
                    and {value.fact_id for value in record.observations}
                    == {item.fact_id}
                    for record in records
                )
                if covered_conflict:
                    legacy_outcomes["covered_by_requirement_plan_conflict"] += 1
                    continue
                if source.kind == "N":
                    currency, proof = requirement_currency(
                        source, requirement_sources, assessed_on
                    )
                    if (
                        currency != "current_mandatory"
                        or not source.point_id
                        or not re.search(r"(?m)^\s*\d+(?:\.\d+)+\s", source.text)
                    ):
                        check = replace(
                            check,
                            status="not_evaluable",
                            reason_code="normative_edition_or_binding_unverified",
                        )
                    else:
                        check = replace(
                            check,
                            source_ids=tuple(
                                dict.fromkeys(
                                    (source.source_id, *(p["source_id"] for p in proof))
                                )
                            ),
                            calculation={
                                **(check.calculation or {}),
                                "assessed_on": assessed_on,
                                "currency": currency,
                            },
                        )
                duplicate = any(
                    part.rule == "requirement_plan"
                    and part.status in {"confirmed", "no_finding"}
                    and part.object_key == check.object_key
                    and part.property_key == check.property_key
                    and source.source_id in part.source_ids
                    and {v.fact_id for v in part.observations} == {item.fact_id}
                    and all(
                        str((part.calculation or {}).get(key)).casefold()
                        == str((check.calculation or {}).get(key)).casefold()
                        for key in ("project", "limit", "operator", "dimension")
                    )
                    for part in checks
                )
                legacy_outcomes[
                    "covered_by_requirement_plan" if duplicate else check.reason_code
                ] += 1
                if not duplicate:
                    checks.append(check)
    if plan is not None:
        plan["legacy_checks"] = dict(legacy_outcomes)
    constraints = [
        check
        for check in checks
        if check.rule in {"requirement_applicability", "requirement_plan"}
        and check.status in {"confirmed", "no_finding"}
        and all(
            key in check.calculation
            for key in ("source_kind", "dimension", "limit", "operator", "source_quote")
        )
    ]
    replacements = {}
    conflicts = {}
    for normative in constraints:
        if normative.calculation["source_kind"] != "N":
            continue
        for customer in constraints:
            if (
                customer.calculation["source_kind"] not in {"T", "U"}
                or normative.object_key != customer.object_key
                or normative.property_key != customer.property_key
            ):
                continue
            n, c = normative.calculation, customer.calculation
            if n["dimension"] != c["dimension"] or scope_key(
                normative.observations[0].fact
            ) != scope_key(customer.observations[0].fact):
                continue
            minimum = {"не менее", ">=", "≥"}
            opposite = (n["operator"] in minimum) != (c["operator"] in minimum)
            low, high = (n, c) if n["operator"] in minimum else (c, n)
            if not opposite or Decimal(low["limit"]) <= Decimal(high["limit"]):
                continue
            source_ids = (*normative.source_ids, *customer.source_ids)
            conflicts[source_ids] = EngineeringCheck(
                "requirement_conflict",
                "needs_review",
                customer.object_key,
                customer.property_key,
                customer.observations,
                "customer_requirement_conflicts_with_norm",
                {"normative": n["source_quote"], "customer": c["source_quote"]},
                source_ids=source_ids,
            )
            replacements[customer.check_id] = replace(
                customer,
                status="not_evaluable",
                reason_code="resolve_conflicting_customer_requirement",
                missing_data=("non_conflicting_customer_requirement",),
            )
    checks = [replacements.get(check.check_id, check) for check in checks]
    checks.extend(conflicts.values())
    # Зависимость обоснования замкнута только при наличии вычисления, источника и разрешённой ссылки.
    resolved = {
        check.observations[0].fact_id
        for check in checks
        if check.rule == "reference_resolution" and check.status == "no_finding"
    }
    by_fact = {}
    for part in checks:
        if part.rule == "justification_chain":
            continue
        for item in part.observations:
            by_fact.setdefault(item.fact_id, []).append(part)
    decisions = {
        (
            decision.object_key,
            decision.decision_type,
            tuple(item.evidence_id for item in decision.observations),
        ): decision
        for decision in registry.decisions
    }
    evidence = {item.evidence_id: item.observation for item in registry.evidence}
    eligible_ids = {
        item.evidence_id
        for item in registry.evidence
        if program_evidence_eligible(item)
    }
    for index, check in enumerate(checks):
        if check.rule != "justification_chain":
            continue
        decision = decisions.get(
            (
                check.object_key,
                check.property_key,
                tuple(item.fact_id for item in check.observations),
            )
        )
        if decision is None:
            continue
        linked = {item.fact_id for item in check.observations}
        relevant = {
            part.check_id: part
            for identity in linked
            for part in by_fact.get(identity, ())
        }.values()
        calculations = [
            part
            for part in relevant
            if part.rule
            not in {
                "justification_chain",
                "reference_resolution",
                "requirement_applicability",
                "requirement_plan",
                "requirement_plan_conflict",
            }
            and linked & {item.fact_id for item in part.observations}
        ]
        requirements = [
            part
            for part in relevant
            if part.rule in {"requirement_applicability", "requirement_plan"}
            and linked & {item.fact_id for item in part.observations}
        ]
        dependencies_grounded = {
            *linked,
            *decision.design_input_ids,
            *decision.calculation_ref_ids,
        } <= eligible_ids
        complete = (
            dependencies_grounded
            and set(decision.purpose_evidence_ids) <= eligible_ids
            and decision.purpose_evidence_ids
            and dict(decision.completeness).get("identity") == "observed"
            and decision.design_input_ids
            and decision.calculation_ref_ids
            and set(decision.calculation_ref_ids) <= resolved
            and calculations
            and all(part.status == "no_finding" for part in calculations)
            and requirements
            and all(part.status == "no_finding" for part in requirements)
        )
        if complete:
            checks[index] = replace(
                check,
                status="no_finding",
                reason_code="justification_chain_verified",
                missing_data=(),
            )
        elif (
            dependencies_grounded
            and registry.document_context_enabled
            and dict(decision.completeness).get("identity") == "observed"
            and decision.design_input_ids
            and decision.calculation_ref_ids
            and not set(decision.calculation_ref_ids) <= resolved
            and any(
                scalar(item.fact) is not None
                and item.fact.property_type
                not in {
                    "design_input",
                    "calculation_reference",
                    "requirement_reference",
                    "document_reference",
                }
                and item.fact.relation not in {"design_input", "input_for"}
                for item in check.observations
            )
        ):
            additional = tuple(
                evidence[key]
                for key in (*decision.design_input_ids, *decision.calculation_ref_ids)
                if key not in linked
            )
            checks[index] = replace(
                check,
                status="needs_review",
                reason_code="specific_calculation_unresolved",
                observations=(*check.observations, *additional),
                calculation={
                    "accepted": [
                        item.fact.value_raw + " " + item.fact.unit_raw
                        for item in check.observations
                    ],
                    "inputs": [
                        evidence[key].fact.evidence_text
                        for key in decision.design_input_ids
                    ],
                    "references": [
                        evidence[key].fact.value_raw
                        for key in decision.calculation_ref_ids
                    ],
                    "checked_pages": sorted(page_texts),
                },
                missing_data=("resolve_calculation_and_verify_accepted_value",),
            )
    if plan is not None:
        plan["legacy_records"] = [
            {
                "check_id": check.check_id,
                "source_kind": (check.calculation or {}).get("source_kind", ""),
                "source_ids": list(check.source_ids),
                "object_key": check.object_key,
                "property_key": check.property_key,
                "result": check.status,
                "reason_code": check.reason_code,
            }
            for check in checks
            if check.rule == "requirement_applicability"
        ]
    return EngineeringReport(registry, tuple(checks), plan)


def requirement_evidence(*, normative=(), technical=(), user=()):
    """Не меняет роли источников и не включает опыт E в доказательства."""
    return tuple(
        RequirementEvidence(
            kind,
            source.source_id,
            source.text,
            source.page,
            source.source_file or "",
            source.point_id,
            getattr(source, "atomic_requirement", None),
        )
        for kind, sources in (("N", normative), ("T", technical), ("U", user))
        for source in sources
    )


def program_findings(
    report: EngineeringReport, *, normative=(), technical=(), user=()
) -> tuple[FindingDraft, ...]:
    """Создаёт только доказанные выводы и конкретные вопросы о реально прочитанных ссылках."""
    findings = []
    specific_refs = {
        item.fact_id
        for check in report.checks
        if check.reason_code == "specific_calculation_unresolved"
        for item in check.observations
        if item.fact.property_type == "calculation_reference"
    }
    for check in report.checks:
        reference_question = (
            report.registry.document_context_enabled
            and check.rule == "reference_resolution"
            and check.status == "not_evaluable"
            and check.reason_code == "referenced_material_not_observed"
        )
        if check.status not in {"confirmed", "needs_review"} and not reference_question:
            continue
        if reference_question and check.observations[0].fact_id in specific_refs:
            continue
        if not check.observations:
            continue
        status = "confirmed" if check.status == "confirmed" else "needs_review"
        first = check.observations[0]
        sources = (
            tuple(_document_source(item) for item in check.observations)
            if report.registry.document_context_enabled
            else ()
        )
        source_identity = tuple(
            sorted(
                (kind, str(source.page), source.text)
                for kind, selected in (("N", normative), ("T", technical), ("U", user))
                for source in selected
                if source.source_id in check.source_ids
            )
        )
        inputs_identity = tuple(
            sorted(
                {
                    (
                        item.fact.identifier,
                        item.fact.property_type,
                        item.fact.property_name,
                        item.fact.value_raw,
                        item.fact.unit_raw,
                        item.fact.relation,
                        item.fact.row_label,
                        item.fact.column_label,
                    )
                    for item in check.observations
                }
            )
        )
        proof_key = stable_id(
            "program-check:",
            (
                check.rule,
                check.reason_code,
                check.calculation,
                source_identity,
                inputs_identity,
            ),
        )
        table_scope = (
            tuple(
                sorted(
                    {
                        "table:" + item.fact.table_id
                        for item in check.observations
                        if item.fact.table_id
                    }
                )
            )
            if check.rule in {"table_sum", "procurement_quantity"}
            else ()
        )
        local_identity = any(
            dict(decision.completeness).get("identity") == "ambiguous"
            and any(item.evidence_id == first.fact_id for item in decision.observations)
            for decision in report.registry.decisions
        )
        assertion = EngineeringAssertion(
            check.object_key,
            "program:" + check.property_key,
            "contradiction" if status == "confirmed" else "documentation",
            (
                *scope_key(first.fact),
                proof_key,
                *table_scope,
                *((f"local-page:{first.page}",) if local_identity else ()),
            ),
            check.observations,
            "deterministic",
            "engineering_program",
        )
        comment, recommendation = _wording(check)
        selected_n = tuple(
            source for source in normative if source.source_id in check.source_ids
        )
        selected_t = tuple(
            source for source in technical if source.source_id in check.source_ids
        )
        selected_u = tuple(
            source for source in user if source.source_id in check.source_ids
        )
        findings.append(
            FindingDraft(
                finding_id="program-" + check.check_id.removeprefix("check-"),
                page=first.page,
                page_type="document",
                category="normative_control"
                if selected_n
                else "customer_requirements"
                if selected_t or selected_u
                else "document_consistency",
                severity="warning",
                status=status,
                comment=comment,
                evidence="; ".join(
                    f"стр. {item.page}: {item.fact.evidence_text}"
                    for item in check.observations
                ),
                recommendation_draft=recommendation,
                confidence=0.95 if status == "confirmed" else 0.65,
                normative_source_ids=tuple(source.source_id for source in selected_n),
                basis="",
                basis_sources=selected_n,
                experience_query="",
                technical_assignment_source_ids=tuple(
                    source.source_id for source in selected_t
                ),
                technical_assignment_basis_sources=selected_t,
                user_package_source_ids=tuple(
                    source.source_id for source in selected_u
                ),
                user_package_basis_sources=selected_u,
                visual_regions=tuple(
                    FindingVisualRegion(**region)
                    for region in first.fact.visual_regions
                )
                if check.rule.startswith("requirement_plan")
                else (),
                object_ref=first.fact.identifier,
                document_context_source_ids=tuple(
                    source.source_id for source in sources
                ),
                document_context_basis_sources=sources,
                evidence_locations=tuple(
                    {
                        "source_id": source.source_id,
                        "page": source.page,
                        "fact_id": source.fact_id,
                        "text": source.evidence_text,
                        "visual_regions": [
                            region.as_dict() for region in source.visual_regions
                        ],
                    }
                    for source in sources
                )
                or (
                    tuple(
                        {
                            "page": item.page,
                            "fact_id": item.fact_id,
                            "text": item.fact.evidence_text,
                            "visual_regions": list(item.fact.visual_regions),
                        }
                        for item in check.observations
                    )
                    if check.rule.startswith("requirement_plan")
                    else ()
                ),
                origin_assertions=(
                    assertion.as_origin(),
                    {
                        "origin": "programmatic_engineering_check",
                        "document_id": report.registry.document_id,
                        "check": check.as_dict(),
                        "checked_pages": sorted(
                            {item.observation.page for item in report.registry.evidence}
                        ),
                    },
                ),
            )
        )
    return tuple(findings)


def prepare_page_program(
    *,
    page_number,
    page_facts,
    extracted_text,
    normative=(),
    technical=(),
    user=(),
    requirement_plan_enabled: bool = False,
):
    """Проверяет исходные факты и переданные основания до смыслового Check Norms."""
    from pdrd_analysis_service.domain.engineering_facts import (
        merge_text_engineering_facts,
    )
    from pdrd_analysis_service.domain.project_dependencies import (
        explicit_project_dependencies,
    )

    facts = merge_text_engineering_facts(
        page_facts.document_facts, extracted_text, page_number
    )
    facts = (*facts, *explicit_project_dependencies(extracted_text, page_number))
    execution = current_vision_execution.get()
    namespace = (
        execution.job_key
        if execution is not None
        else stable_id("local-source-", (page_number, extracted_text))
    )
    registry = build_decision_registry(
        document_id=namespace,
        facts=tuple(
            LocatedDocumentFact(page_number, index, fact)
            for index, fact in enumerate(facts, 1)
        ),
        page_texts={page_number: extracted_text},
        document_context_enabled=False,
    )
    report = run_engineering_program(
        registry,
        page_texts={page_number: extracted_text},
        requirement_sources=requirement_evidence(
            normative=normative, technical=technical, user=user
        ),
        requirement_plan_enabled=requirement_plan_enabled,
    )
    return report


def augment_page_findings(
    *,
    findings,
    page_number,
    page_facts,
    extracted_text,
    normative=(),
    technical=(),
    user=(),
    report=None,
):
    """Применяет уже вычисленную программу к существующим кандидатам без повторного чтения."""
    from pdrd_analysis_service.application.decision_protocol import (
        attach_decision_protocols,
    )

    report = report or prepare_page_program(
        page_number=page_number,
        page_facts=page_facts,
        extracted_text=extracted_text,
        normative=normative,
        technical=technical,
        user=user,
    )
    registry = report.registry
    findings = audit_engineering_candidates(findings, report)
    findings = audit_findings_evidence(
        findings,
        page_texts={page_number: extracted_text},
        stage="check_norms_evidence_audit",
    )
    combined = {
        finding.finding_id: finding
        for finding in (
            *findings,
            *program_findings(
                report, normative=normative, technical=technical, user=user
            ),
        )
    }
    return attach_decision_protocols(
        tuple(combined.values()), registry, checks=report.checks
    )


def audit_engineering_candidates(findings, report):
    """Проверяет структурный предмет арифметики, сохраняя неопределённость и контрдоказательства."""
    result = []
    for finding in findings:
        if any(
            origin.get("origin") == "programmatic_engineering_check"
            for origin in finding.origin_assertions
        ):
            result.append(finding)
            continue
        assertion = read_assertion(finding.origin_assertions)
        observations = assertion.observations if assertion else ()
        arithmetic_claim = (
            assertion is not None and assertion.claim_type == "arithmetic"
        ) or any(
            origin.get("origin") == "vlm" and origin.get("claim_type") == "arithmetic"
            for origin in finding.origin_assertions
        )
        totals = tuple(
            item
            for item in observations
            if item.fact.relation in {"sum_total", "table_total"}
        )
        structural_sum = (
            bool(totals)
            and (
                len(totals) == 1
                or any(item.fact.relation == "sum_component" for item in observations)
            )
            and (
                len({item.page for item in observations}) == 1
                or any(item.fact.relation == "sum_component" for item in observations)
            )
            and all(
                item.fact.relation
                in {
                    "sum_total",
                    "sum_component",
                    "table_total",
                    "table_cell",
                    "table_end",
                }
                for item in observations
            )
        )
        cells = tuple(
            item.observation
            for item in report.registry.evidence
            if item.observation.page == finding.page
            and item.observation.fact.column_binding == "pdf_geometry"
        )
        wrong_units = []
        for observed in observations:
            for actual in cells:
                a, b = observed.fact, actual.fact
                if (
                    observed.page == actual.page
                    and a.unit_raw
                    and column_unit_key(a.unit_raw) != column_unit_key(b.unit_raw)
                    and _same_cell_value(a.value_raw, b.value_raw)
                    and normalize_label(a.row_label or a.subject_name)
                    == normalize_label(b.row_label)
                    and {region.get("role") for region in b.source_regions}
                    >= {"column_header", "cell"}
                ):
                    wrong_units.append(actual)
        if not arithmetic_claim and not structural_sum and not wrong_units:
            result.append(finding)
            continue
        if arithmetic_claim and not totals:
            # Числа связывают предмет с реальной ячейкой; слова замечания не являются фильтром.
            numbers = {
                value.replace(",", ".")
                for value in re.findall(
                    r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w.])", finding.evidence
                )
            }
            totals = tuple(
                item
                for item in cells
                if item.fact.relation == "table_total"
                and any(
                    _same_cell_value(item.fact.value_raw, value) for value in numbers
                )
            )
        resolved_totals = []
        for total in totals:
            matches = [
                item
                for item in cells
                if item.fact.relation == "table_total"
                and item.page == total.page
                and _same_cell_value(item.fact.value_raw, total.fact.value_raw)
                and column_unit_key(item.fact.unit_raw)
                == column_unit_key(total.fact.unit_raw)
            ]
            # Только единственная ячейка своего листа: неоднозначную привязку не выбираем.
            resolved_totals.append(matches[0] if len(matches) == 1 else total)
        totals = tuple(resolved_totals)
        tables = {item.fact.table_id for item in totals}
        checks = [
            check
            for check in report.checks
            if check.rule == "table_sum"
            and any(
                total.fact_id == item.fact_id
                or (
                    total.page == item.page
                    and total.fact.table_id == item.fact.table_id
                    and total.fact.column_label == item.fact.column_label
                    and total.fact.calculation_group == item.fact.calculation_group
                    and total.fact.source_row_ids == item.fact.source_row_ids
                )
                for total in totals
                for item in check.observations
                if item.fact.relation in {"sum_total", "table_total"}
            )
        ]
        reason = "table_calculation_not_established"
        status, comment, recommendation = (
            "hypothesis",
            finding.comment,
            finding.recommendation_draft,
        )
        if wrong_units:
            reason = "column_unit_counterevidence"
        elif checks and all(check.status == "no_finding" for check in checks):
            reason = "verified_table_counterevidence"
        elif any(check.status == "confirmed" for check in checks):
            # Каноническое доказанное замечание добавляет program_findings; текст модели остаётся в истории.
            reason = "superseded_by_verified_table_calculation"
        elif totals and all(
            item.fact.column_binding == "pdf_geometry" for item in totals
        ):
            status = "needs_review"
            label = "; ".join(
                dict.fromkeys(
                    f"{item.fact.row_label}: {item.fact.value_raw} {item.fact.unit_raw}"
                    for item in totals
                )
            )
            comment = (
                f"Уточнить правило формирования показателя «{label}»: "
                "какие строки и режимы входят в расчётную группу."
            )
            recommendation = (
                "Уточнить, отражает итог сумму, производительность или расход выбранного режима, "
                "и предоставить правило расчёта. Ошибка суммы не подтверждена."
            )
            modes = {
                normalize_label(item.fact.scope_operating_mode)
                for item in cells
                if item.fact.scope_operating_mode
                and any(
                    total.fact.table_id == item.fact.table_id
                    and total.fact.calculation_group == item.fact.calculation_group
                    and total.fact.column_label == item.fact.column_label
                    for total in totals
                )
            }
            if len(modes) > 1:
                status, reason = "hypothesis", "mixed_operating_modes_not_additive"
                comment, recommendation = finding.comment, finding.recommendation_draft
        snapshot = tuple(
            item.observation
            for item in report.registry.evidence
            if item.observation.fact.table_id in tables
        ) or tuple(wrong_units)
        previous = next(
            (
                origin
                for origin in finding.origin_assertions
                if origin.get("origin") == "engineering_candidate_gate"
                and origin.get("review_kind") == "table_arithmetic"
            ),
            {},
        )
        origins = finding.origin_assertions
        if status == "needs_review" and totals:
            rows = {
                (
                    item.page,
                    item.fact.table_id,
                    item.fact.row_label,
                    item.fact.calculation_group,
                    scope_key(item.fact)[:6],
                )
                for item in totals
            }
            if len(rows) == 1 and all(row[1] and row[2] for row in rows):
                question = EngineeringAssertion(
                    "table:" + totals[0].fact.table_id,
                    "table:total_rule",
                    "arithmetic",
                    (
                        stable_id(
                            "table-rule:",
                            (
                                tuple(sorted(rows)),
                                tuple(
                                    sorted(
                                        {
                                            (
                                                check.rule,
                                                check.reason_code,
                                                check.missing_data,
                                            )
                                            for check in checks
                                        }
                                    )
                                ),
                            ),
                        ),
                    ),
                    totals,
                    "unverified",
                    "table_total_question",
                )
                origins = (
                    question.as_origin(),
                    *(
                        {**origin, "origin": "engineering_assertion_history"}
                        if origin.get("origin") == "engineering_assertion"
                        else origin
                        for origin in origins
                    ),
                )
        result.append(
            replace(
                finding,
                status=status,
                comment=comment,
                recommendation_draft=recommendation,
                origin_assertions=(
                    *(origin for origin in origins if origin is not previous),
                    {
                        "origin": "engineering_candidate_gate",
                        "review_kind": "table_arithmetic",
                        "reason_code": reason,
                        "original_status": previous.get(
                            "original_status", finding.status
                        ),
                        "original_comment": previous.get(
                            "original_comment", finding.comment
                        ),
                        "original_recommendation": previous.get(
                            "original_recommendation", finding.recommendation_draft
                        ),
                        "document_id": report.registry.document_id,
                        "document_context_enabled": report.registry.document_context_enabled,
                        "facts": [asdict(item) for item in snapshot],
                        "checks": [check.as_dict() for check in checks],
                    },
                ),
            )
        )
    return tuple(result)


def _same_cell_value(left, right):
    """Числовая идентичность не зависит от записанных нулей, но не превращает пустоту в ноль."""
    pattern = r"[+-]?\d+(?:[.,]\d+)?"
    return bool(
        re.fullmatch(pattern, left)
        and re.fullmatch(pattern, right)
        and Decimal(left.replace(",", ".")) == Decimal(right.replace(",", "."))
    )


def finalize_arithmetic_candidate(finding):
    """Повторяет табличный контроль по snapshot перед финализацией, без доверия одному статусу."""
    gate = next(
        (
            origin
            for origin in finding.origin_assertions
            if origin.get("origin") == "engineering_candidate_gate"
            and origin.get("review_kind") == "table_arithmetic"
        ),
        None,
    )
    assertion = read_assertion(finding.origin_assertions)
    typed = any(
        origin.get("origin") == "vlm" and origin.get("claim_type") == "arithmetic"
        for origin in finding.origin_assertions
    )
    if (
        gate is None
        and not typed
        and not (assertion and assertion.claim_type == "arithmetic")
    ):
        return finding
    try:
        observations = tuple(
            LocatedDocumentFact(
                raw["page"], raw["index"], document_fact_from_mapping(raw["fact"])
            )
            for raw in (gate or {}).get("facts", ())
        )
        if any(item.fact is None for item in observations):
            raise ValueError("Некорректный snapshot таблицы")
        texts = {}
        for item in observations:
            texts.setdefault(item.page, []).append(item.fact.evidence_text)
        registry = build_decision_registry(
            document_id=(gate or {}).get("document_id", "final-arithmetic"),
            facts=observations,
            page_texts={page: "\n".join(parts) for page, parts in texts.items()},
            document_context_enabled=(gate or {}).get(
                "document_context_enabled", False
            ),
        )
        report = run_engineering_program(
            registry,
            page_texts={page: "\n".join(parts) for page, parts in texts.items()},
        )
        original = replace(
            finding,
            status=(gate or {}).get("original_status", finding.status),
            comment=(gate or {}).get("original_comment", finding.comment),
            recommendation_draft=(gate or {}).get(
                "original_recommendation", finding.recommendation_draft
            ),
        )
        return audit_engineering_candidates((original,), report)[0]
    except (KeyError, TypeError, ValueError, AttributeError):
        return replace(finding, status="hypothesis")


def protected_table_question(finding):
    """Защищает воспроизводимый конкретный вопрос о табличном итоге от уверенного ответа VLM."""
    if finding.status != "needs_review" or not any(
        origin.get("review_kind") == "table_arithmetic"
        for origin in finding.origin_assertions
    ):
        return False
    checked = finalize_arithmetic_candidate(finding)
    return (
        checked.status == finding.status
        and checked.comment == finding.comment
        and checked.recommendation_draft == finding.recommendation_draft
    )


def _document_source(item):
    """Сохраняет исходное доказательство, не создавая новые геометрические рамки."""
    return DocumentContextSource(
        source_id="D-" + item.fact_id,
        page=item.page,
        fact_id=item.fact_id,
        text=fact_search_text(item),
        evidence_text=item.fact.evidence_text,
        score=1.0,
        match_type="programmatic_check",
        subject=item.fact.subject_name,
        property=item.fact.property_name,
        scope="; ".join(scope_key(item.fact)),
        visual_regions=tuple(
            FindingVisualRegion(**region) for region in item.fact.visual_regions
        ),
    )


def _wording(check):
    values = check.calculation or {}
    obj = (
        check.observations[0].fact.subject_name or check.observations[0].fact.identifier
    )
    if check.rule == "justification_chain":
        return (
            f"Для {obj} по параметру «{check.observations[0].fact.property_name}» "
            f"принято {', '.join(values['accepted'])}; известны исходные данные: {'; '.join(values['inputs'])}. "
            f"Ссылка на расчёт {', '.join(values['references'])} не разрешена в проверенных материалах; "
            "связь принятого значения с исходными данными требует проверки.",
            "Предоставить указанный расчёт или уточнить ссылку и проверить получение принятого значения "
            "из перечисленных исходных данных.",
        )
    if check.rule == "explicit_arithmetic":
        return (
            f"В записанном равенстве {values['expression']}={values['stated']} результат не совпадает "
            f"с вычисленным {values['expected']} {values['unit']}.",
            "Проверить исходное равенство и исправить результат вычисления.",
        )
    if check.rule == "topic_reference_consistency":
        return (
            f"Для одного предмета «{obj}» даны разные адреса: {', '.join(values['targets'])}. "
            "Повторные указания не определяют однозначную внутреннюю ссылку.",
            "Проверить содержимое указанных таблиц, приложений или разделов и уточнить адрес каждой ссылки.",
        )
    if check.rule == "design_basis_dependency":
        pages = ", ".join(map(str, values["checked_pages"]))
        if values["dependency"] == "fire_storage_basis":
            return (
                f"Принят состав резервуаров: {values['accepted']}. Уточнить расчётное обоснование запаса: "
                "полезный объём в расчётном режиме, расход и продолжительность пожаротушения, "
                "гарантированное пополнение и доступность запаса при резервировании. "
                f"В проверенных материалах (стр. {pages}) связанная цепочка этих исходных данных не установлена.",
                "Предоставить водный баланс с условиями применения нормы. Геометрические объёмы резервуаров "
                "не приравниваются к полезному запасу; его недостаточность не доказана.",
            )
        if values["dependency"] == "protected_object_basis":
            return (
                f"Для защищаемого объекта «{obj}» принято решение: {values['accepted']}. "
                "Уточнить применимые требования к этому объекту и увязать с ними вид пожаротушения, "
                "исходные характеристики объекта, расчётный расход и отображение защиты на схеме. "
                f"В проверенных материалах (стр. {pages}) эта связь не установлена.",
                "Привести категорию и характеристики конкретного объекта, применимый нормативный пункт "
                "и расчётную либо графическую привязку принятого решения. Отсутствие защиты не доказано.",
            )
        if values["dependency"] == "fire_flow_basis":
            return (
                f"Принят расход {values['accepted']} для решения «{obj}». Указать расчётное и нормативное обоснование "
                "этого пожарного расхода: назначение и характеристики защищаемого объекта, расчётные условия "
                f"и применимое требование. В проверенных материалах (стр. {pages}) цепочка основания не установлена.",
                "Связать принятое значение с исходными условиями и применимым пунктом нормы; "
                "нарушение численного требования без этих условий не подтверждено.",
            )
        labels = {
            "design_flow": "расчётный расход",
            "internal_diameter": "внутренний диаметр",
            "flow_velocity": "скорость",
            "head_loss": "потери напора",
            "calculation_segment": "границы расчётного участка",
            "operating_mode": "расчётный режим",
        }
        missing = ", ".join(labels[key] for key in values["missing"])
        return (
            f"Для {obj} принят наружный диаметр {values['accepted']}. Уточнить гидравлическое обоснование "
            f"выбора: не установлены связанные показатели ({missing}) для одного расчётного участка и режима "
            f"в проверенных материалах (стр. {pages}).",
            "Указать расчётный участок, режим и связанные расход, внутренний диаметр, скорость и потери; "
            "DN не подставлять вместо внутреннего диаметра. Коэффициенты и условия должны следовать из расчёта.",
        )
    if check.rule == "procurement_quantity":
        return (
            f"Для {obj} требуется {values['required']} шт., "
            f"в сопоставленной закупочной ведомости {values['purchased']} шт.; дефицит {values['deficit']} шт.",
            "Сверить состав и исполнение изделий и дополнить закупочную ведомость.",
        )
    if check.rule == "table_sum":
        return (
            f"В полной таблице итог {values['stated']} не совпадает "
            f"с суммой строк {values['expected']} {_dimension_label(values['dimension'])}.",
            "Сверить перечисленные строки и исправить итог с учётом единиц и режима.",
        )
    if check.rule == "available_head":
        available, required = check.observations[:2]
        return (
            f"Для {obj} располагаемый напор {available.fact.value_raw} {available.fact.unit_raw} "
            f"ниже явно требуемого {required.fact.value_raw} {required.fact.unit_raw} в одном режиме.",
            "Проверить баланс напоров по указанным исходным данным и режиму.",
        )
    if check.rule == "fire_reserve":
        return (
            f"Для {obj} полезный пожарный запас {values['accepted_m3']} м³ "
            f"меньше расчётной потребности {values['required_m3']} м³ "
            "при известных расходе, длительности и гарантированной подпитке.",
            "Уточнить полезный запас или его расчётные исходные данные по приведённому водному балансу.",
        )
    if check.rule == "reference_resolution":
        pages = ", ".join(map(str, values.get("checked_pages", [])))
        return (
            f"Ссылка «{values.get('target', '')}» не разрешена среди доступных страниц {pages}. "
            "Требуется проверить адрес ссылки или предоставить связанный материал.",
            "Уточнить конкретный адрес ссылки; отсутствие материала во всём проекте не установлено.",
        )
    if check.rule in {"velocity_balance", "head_loss_balance"}:
        label = (
            "скорость"
            if check.rule == "velocity_balance"
            else "суммарные потери напора"
        )
        return (
            f"Для {obj} указаны {label} {values['stated']} {values['unit']}; "
            f"по явно приведённым исходным данным получено {values['expected']} {values['unit']} "
            f"по зависимости {values['formula']}. Расхождение превышает допуск округления.",
            "Сверить исходные параметры того же участка и режима и исправить значение либо расчёт.",
        )
    if check.rule == "hydraulic_dependency":
        return (
            f"Для {obj} принято {values['accepted']}; известны исходные данные: "
            f"{'; '.join(values['inputs'])}. Для проверки зависимости не установлены: "
            f"{'; '.join(values['missing'])}.",
            "Предоставить перечисленные исходные данные для того же участка и режима; "
            "без них ошибочность принятого значения не подтверждена.",
        )
    if check.rule == "requirement_applicability":
        project = check.observations[0].fact
        return (
            f"Для {obj} принято {project.value_raw} {project.unit_raw}, "
            f"а переданный источник требует «{values['source_quote']}» в сопоставимых условиях.",
            "Сверить принятое значение с указанным требованием и исходными условиями.",
        )
    if check.rule == "requirement_plan":
        address = (
            f"{values['document']}, п. {values['clause']}"
            if values["clause"]
            else values["document"]
        )
        if values["rule"] == "required_calculation":
            missing = values.get("missing_parameters", [])
            detail = " Уточнить: " + "; ".join(missing) + "." if missing else ""
            return (
                f"Для {obj} источник «{address}» прямо требует расчётного обоснования: «{values['quote']}». "
                "По переданным фактам этого объекта требуется уточнить связь принятого решения с расчётом."
                + detail,
                "Указать расчёт и связь с принятым решением; отсутствие расчёта во всём проекте не установлено.",
            )
        if values["rule"] in {
            "formula",
            "required_component",
            "table_row",
            "conditional_bound",
        }:
            observed = "; ".join(
                f"{item.fact.property_name}: {item.fact.value_raw} {item.fact.unit_raw}".strip()
                for item in check.observations
            )
            details = ""
            if values["rule"] == "formula":
                details = (
                    f" Формула: {values['expression']}; допустимый по исходным данным интервал: "
                    f"{values['calculated_min']}…{values['calculated_max']}."
                )
            elif values.get("conditions"):
                details = f" Проверенные условия: {values['conditions']}."
            elif values.get("table_row"):
                details = (
                    " Выбранная строка таблицы: "
                    + " | ".join(values["table_row"])
                    + "."
                )
            return (
                f"Для {obj} установлено: {observed}. Применимое требование «{address}»: «{values['quote']}»."
                + details
                + " Программная проверка установила несовместимость исходных данных с этим требованием.",
                "Проверить указанные исходные данные, условия применения и привести принятое решение в соответствие.",
            )
        project = check.observations[0].fact
        return (
            f"Для {obj} принято {project.property_name}: {project.value_raw} {project.unit_raw}. "
            f"Применимое требование «{address}»: «{values['quote']}». "
            "Сопоставленные значение и ограничение несовместимы с учётом единиц и допуска округления.",
            "Проверить приведённое требование для указанных условий и привести принятое значение в соответствие.",
        )
    if check.rule == "requirement_plan_conflict":
        return (
            f"Для {obj} несовместимы нормативное требование «{values['normative']}» "
            f"и дополнительное требование «{values['customer']}» в одних условиях.",
            "Уточнить противоречащее ТЗ или пользовательское требование; "
            "оно не отменяет применимое нормативное ограничение.",
        )
    if check.rule == "requirement_conflict":
        return (
            f"Для {obj} переданные требования несовместимы: нормативный источник — «{values['normative']}», "
            f"проектное требование — «{values['customer']}».",
            "Проверить применимость указанных требований и уточнить ТЗ или пользовательское условие; "
            "оно не может ослаблять применимое нормативное требование.",
        )
    if check.rule == "signal_compatibility":
        return (
            f"Прямое соединение {check.object_key}: выход {values['output']} В {values['source_type']} "
            "не входит в явно допустимые параметры входа "
            f"{values['input_low']}…{values['input_high']} В {values['target_type']}.",
            "Проверить конкретные концы соединения и предусмотренное согласующее устройство.",
        )
    label = {
        "signal_function": "назначения сигналов",
        "contact_state": "состояния контактов",
        "safe_state": "безопасные состояния",
        "control_mode": "режимы управления",
    }.get(check.property_key, "функциональные параметры")
    return (
        f"Для соединения {check.object_key} различаются явно прочитанные {label}; "
        "необходимо подтвердить функциональную логику.",
        "Сверить функцию, режим и безопасное состояние по фактической схеме.",
    )


def _dimension_label(dimension):
    """Пользователь видит физическую единицу, а не внутренний ключ размерности."""
    return {
        "flow_day": "м³/сут",
        "flow_m3h": "м³/ч",
        "volume_m3": "м³",
        "count": "шт",
        "length_mm": "мм",
        "pressure_pa": "Па",
        "power_w": "Вт",
    }.get(dimension, "")


def verified_programmatic_check(finding: FindingDraft) -> bool:
    """Повторно вычисляет доказательство, не доверяя флагу confirmed в происхождении."""
    origin = next(
        (
            origin
            for origin in finding.origin_assertions
            if origin.get("origin") == "programmatic_engineering_check"
        ),
        None,
    )
    if origin is None:
        return False
    try:
        raw = origin["check"]
        observations = tuple(
            LocatedDocumentFact(
                item["page"], item["index"], document_fact_from_mapping(item["fact"])
            )
            for item in raw["observations"]
        )
        if any(item.fact is None for item in observations):
            return False
        by_page = {}
        for item in observations:
            by_page.setdefault(item.page, []).append(item.fact.evidence_text)
        registry = build_decision_registry(
            document_id=origin["document_id"],
            facts=observations,
            page_texts={page: "\n".join(texts) for page, texts in by_page.items()},
        )
        sources = requirement_evidence(
            normative=finding.basis_sources,
            technical=finding.technical_assignment_basis_sources,
            user=finding.user_package_basis_sources,
        )
        report = run_engineering_program(
            registry,
            page_texts={page: "\n".join(texts) for page, texts in by_page.items()},
            requirement_sources=sources,
            requirement_plan_enabled=raw["rule"].startswith("requirement_plan"),
            assessed_on=(raw.get("calculation") or {}).get("assessed_on"),
        )
        for check in report.checks:
            if (
                check.rule != raw["rule"]
                or check.object_key != raw["object_key"]
                or check.property_key != raw["property_key"]
            ):
                continue
            if check.reason_code != raw["reason_code"] or check.status != raw["status"]:
                continue
            rendered = check
            if check.rule == "design_basis_dependency" and any(
                (check.calculation or {}).get(key)
                != (raw.get("calculation") or {}).get(key)
                for key in ("accepted", "dependency", "missing")
            ):
                return False
            if check.rule in {"reference_resolution", "design_basis_dependency"}:
                rendered = replace(check, calculation=raw["calculation"])
            comment, recommendation = _wording(rendered)
            if (
                finding.comment != comment
                or finding.recommendation_draft != recommendation
            ):
                return False
            if check.status == "confirmed":
                return (
                    finding.status == "confirmed"
                    and check.calculation == raw["calculation"]
                )
            if check.status == "needs_review":
                return finding.status == "needs_review"
            if check.rule == "reference_resolution" and check.status == "not_evaluable":
                return finding.status == "needs_review"
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    return False


def verified_equipment_check(finding: FindingDraft) -> bool:
    """Повторяет существующее сравнение точного изделия, проверяя сохранённый EQ snapshot."""
    from pdrd_analysis_service.domain.equipment import (
        EquipmentFact,
        EquipmentOccurrence,
        InventoryItem,
        compare_equipment,
    )

    origin = next(
        (
            item
            for item in finding.origin_assertions
            if item.get("origin") == "equipment_program_check"
        ),
        None,
    )
    if origin is None or finding.status != "confirmed":
        return False
    try:
        raw = dict(origin["item"])
        raw["occurrences"] = tuple(
            EquipmentOccurrence(**item) for item in raw["occurrences"]
        )
        item = InventoryItem(**raw)
        comparisons = compare_equipment(
            item, tuple(EquipmentFact(**fact) for fact in origin["facts"])
        )
        for comparison in comparisons:
            if (
                comparison.candidate_id != finding.finding_id
                or comparison.status != "mismatch_candidate"
            ):
                continue
            source = next(
                (
                    source
                    for source in finding.equipment_documentation_basis_sources
                    if source.source_id == comparison.source_id
                ),
                None,
            )
            if source is None:
                return False
            return (
                finding.comment == equipment_proof_comment(item, comparison)
                and finding.evidence == comparison.project_evidence
                and source.trust_status == "trusted"
                and source.page == comparison.source_page
                and source.manufacturer == item.manufacturer
                and source.model == item.model
                and source.variant == item.variant
                and source.snippet == comparison.manufacturer_evidence
                and source.value_raw == comparison.manufacturer_value
                and len(source.sha256) == 64
                and source.source_url.startswith(("http://", "https://"))
            )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    return False


def equipment_proof_comment(item, comparison):
    """Единая формулировка паспортного сравнения не содержит выводов о последствиях аварии."""
    project = f"{comparison.project_value} {comparison.project_unit}".strip()
    maker = f"{comparison.manufacturer_value} {comparison.manufacturer_unit}".strip()
    return (
        f"{item.manufacturer} {item.model}: {comparison.property_name} в проекте {project}; "
        f"документация производителя указывает {maker}. {comparison.explanation}"
    )


def equipment_review_question(finding):
    """Не выдаёт неподтверждённый EQ вывод за доказанное нарушение и не запрашивает VLM."""
    if (
        finding.category != "equipment"
        or not finding.equipment_documentation_basis_sources
    ):
        return finding
    if verified_equipment_check(finding) or finding.status != "confirmed":
        return finding
    details = finding.equipment_details
    comment = (
        f"Проверить для {details.get('manufacturer', '')} {details.get('model', '')} "
        f"параметр {details.get('property_name', '')}: "
        f"в проекте {details.get('project_value', '')} {details.get('project_unit', '')}; "
        "применимость сохранённых характеристик оборудования программно не подтверждена."
    )
    return replace(
        finding,
        status="needs_review",
        comment=comment,
        origin_assertions=(
            *finding.origin_assertions,
            {
                "origin": "equipment_review_limit",
                "reason_code": "exact_equipment_proof_unavailable",
                "original_comment": finding.comment,
            },
        ),
    )


def protected_equipment_question(finding):
    """Конкретная неопределённость сохраняет точную модель, параметр и исходный источник."""
    return (
        finding.category == "equipment"
        and finding.status == "needs_review"
        and bool(finding.evidence.strip())
        and bool(finding.equipment_documentation_basis_sources)
        and any(
            origin.get("origin")
            in {"equipment_review_limit", "equipment_program_check"}
            for origin in finding.origin_assertions
        )
    )
