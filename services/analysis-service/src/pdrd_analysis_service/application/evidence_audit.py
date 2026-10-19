# services/analysis-service/src/pdrd_analysis_service/application/evidence_audit.py

"""Применение проверки доказательств в существующих этапах и origin_assertions."""

from __future__ import annotations

import re
from dataclasses import asdict, replace

from pdrd_analysis_service.application.candidate_trace import trace_candidate
from pdrd_analysis_service.domain.analysis import FindingDraft
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    document_fact_from_mapping,
    property_key,
)
from pdrd_analysis_service.domain.engineering_assertion import (
    assertion_from_facts,
    candidate_parameter_roles,
    candidate_property,
    read_assertion,
)
from pdrd_analysis_service.domain.evidence_audit import (
    EvidenceAudit,
    audit_assertion,
    bind_evidence,
    diagnostic_language,
    literal_contains,
)

AUDIT_ORIGIN = "engineering_evidence_audit"


def _bind_legacy_context(finding):
    """Связывает старый формат только с явно названным объектом и свойством его исходных фактов."""
    if read_assertion(finding.origin_assertions) is not None:
        return finding
    types = {
        origin.get("claim_type")
        for origin in finding.origin_assertions
        if origin.get("origin") == "vlm" and origin.get("claim_type")
    }
    prop = candidate_property(finding.comment + " " + finding.evidence)
    if (
        types == {"documentation"}
        and prop == "Назначение системы"
        and not re.search(r"обоснован|расч[её]т|почему", finding.comment, re.I)
    ):
        types = {"contradiction"}
    if types and not types <= {"contradiction", "missing_justification"}:
        return finding
    if not types and not re.search(
        r"различ|расхожд|несоответств|противореч|обоснован|назван|наименован|обозначен[аоы]?\b",
        finding.comment,
        re.I,
    ):
        return finding
    patterns = {
        "Толщина стенки": r"толщин",
        "Диаметр": r"диаметр|типоразмер",
        "Длина": r"длин|протяж",
        "Назначение системы": r"назначени|наименовани|system_purpose|purpose|system_type",
        "Расход воды": r"расход",
        "Напор": r"напор",
        "Абсолютная отметка": r"отметк|высот",
    }
    if prop not in patterns:
        return finding
    observations = []
    for origin in finding.origin_assertions:
        context = origin.get("context")
        if origin.get("origin") != "engineering_context" or not isinstance(
            context, dict
        ):
            continue
        page = context.get("page")
        if not isinstance(page, int) or page < 1:
            continue
        for index, raw in enumerate(context.get("facts", ()), 1):
            fact = document_fact_from_mapping(raw)
            if fact is None or not re.search(
                patterns[prop], fact.property_name + " " + property_key(fact), re.I
            ):
                continue
            if not fact.subject_type and prop in {
                "Диаметр",
                "Толщина стенки",
                "Длина",
                "Абсолютная отметка",
            }:
                # Старый компактный контракт разрешал пропуск типа. Такая запись
                # достаточна для контрдоказательства эквивалентности, но не для подтверждения объекта.
                fact = replace(fact, subject_type="unclassified")
            roles = candidate_parameter_roles(finding.comment)
            if (
                types == {"missing_justification"}
                and roles
                and not any(
                    role in (fact.property_name + " " + fact.column_label).casefold()
                    for role in roles
                )
            ):
                continue
            if not literal_contains(finding.object_ref, fact.identifier) and not (
                fact.evidence_text and fact.evidence_text in finding.evidence
            ):
                continue
            item = LocatedDocumentFact(page, index, fact)
            if item not in observations:
                observations.append(item)
    if not observations:
        return finding
    claim_type = (
        "missing_justification"
        if re.search(r"обоснован|без.*расч[её]т", finding.comment, re.I)
        else "contradiction"
    )
    if len(types) == 1:
        claim_type = next(iter(types))
    if (
        claim_type == "missing_justification"
        and re.search(
            r"^(?:уточнить|сверить|согласовать)\s+(?:назначение|наименование)\b",
            finding.comment,
            re.I,
        )
        and not re.search(
            r"расч[её]т|исходн\w*\s+услов|почему|обоснован|требован",
            finding.comment,
            re.I,
        )
    ):
        claim_type = "contradiction"
    assertion = assertion_from_facts(
        tuple(observations), claim_type=claim_type, include_related=True
    )
    if assertion is None:
        return finding
    return replace(
        finding, origin_assertions=(*finding.origin_assertions, assertion.as_origin())
    )


def audit_findings_evidence(
    findings: tuple[FindingDraft, ...],
    *,
    page_texts: dict[int, str],
    stage: str,
    located_facts: tuple[LocatedDocumentFact, ...] = (),
) -> tuple[FindingDraft, ...]:
    """Проверяет все переданные кандидаты без изменения источников, страниц и областей."""
    lookup = {
        (
            item.page,
            item.fact.identifier,
            item.fact.property_name,
            item.fact.value_raw,
            item.fact.evidence_text,
        ): item.fact
        for item in located_facts
    }
    result = []
    for finding in findings:
        finding = _bind_legacy_context(finding)
        assertion = read_assertion(finding.origin_assertions)
        if assertion is not None and lookup:
            observations = tuple(
                replace(
                    item,
                    fact=lookup.get(
                        (
                            item.page,
                            item.fact.identifier,
                            item.fact.property_name,
                            item.fact.value_raw,
                            item.fact.evidence_text,
                        ),
                        item.fact,
                    ),
                )
                for item in assertion.observations
            )
            if observations != assertion.observations:
                updated = replace(assertion, observations=observations)
                finding = replace(
                    finding,
                    origin_assertions=(
                        updated.as_origin(),
                        *(
                            {**origin, "origin": "engineering_assertion_history"}
                            if origin.get("origin") == "engineering_assertion"
                            else origin
                            for origin in finding.origin_assertions
                        ),
                    ),
                )
        result.append(_audit_finding(finding, page_texts=page_texts, stage=stage))
    return tuple(result)


def _source_limits(finding):
    """Различает доступный фрагмент и запрос несуществующего N/T/U/D/EQ-источника."""
    missing = []
    for id_field, source_field in (
        ("normative_source_ids", "basis_sources"),
        ("technical_assignment_source_ids", "technical_assignment_basis_sources"),
        ("user_package_source_ids", "user_package_basis_sources"),
        ("document_context_source_ids", "document_context_basis_sources"),
        ("equipment_documentation_source_ids", "equipment_documentation_basis_sources"),
    ):
        sources = {
            source.source_id: source for source in getattr(finding, source_field)
        }
        requested = dict.fromkeys(
            (
                *getattr(finding, id_field),
                *(
                    source_id
                    for origin in finding.origin_assertions
                    if origin.get("origin") == "vlm"
                    for source_id in origin.get(id_field, ())
                    if isinstance(source_id, str)
                ),
            )
        )
        for source_id in requested:
            source = sources.get(source_id)
            text = (
                getattr(source, "text", "")
                or getattr(source, "evidence_text", "")
                or getattr(source, "snippet", "")
            )
            if not text:
                missing.append(source_id)
    return tuple(missing)


def _question(assertion, audit):
    """Различает две прочитанные записи и называет предел именно этого сравнения."""
    observations = tuple(
        item
        for item, binding in zip(assertion.observations, audit.bindings, strict=True)
        if binding.question_anchor
    )
    first = observations[0].fact
    values = "; ".join(
        dict.fromkeys(
            f"стр. {item.page}: {item.fact.value_raw} {item.fact.unit_raw}".strip()
            for item in observations
        )
    )
    subject = first.identifier
    prop = first.property_name
    limitation = {
        "same_physical_segment_not_established": (
            "Принадлежат ли эти записи одному физическому участку? Указать его границы"
        ),
        "incomplete_comparison_evidence": "Уточнить привязку обеих записей к объекту и указанным условиям сравнения",
        "cited_source_unavailable": (
            "Предоставить указанный источник для проверки применимости требования к этому решению"
        ),
        "object_type_not_established": "Уточнить, описывают ли записи один вид объекта",
        "value_semantics_unidentified": "Уточнить определения приведённых показателей и условия их сопоставления",
    }.get(
        audit.reason_code,
        "Уточнить, какое из приведённых значений принято для сопоставленных условий",
    )
    return (
        f"Для {subject} приведены записи «{prop}»: {values}. {limitation}.",
        "Уточнить указанный предмет сравнения по исходной документации; "
        "ошибочность принятого значения пока не доказана.",
    )


def _specific_original_question(finding, assertion):
    """Сохраняет предметный вопрос о недостающей предпосылке, не подменяя его вопросом о самом числе."""
    text = finding.comment
    return (
        assertion.claim_type == "missing_justification"
        and bool(
            re.search(
                r"расч[её]т(?!н)|исходн\w*\s+(?:данн|услов)|нормативн\w*\s+обоснован|"
                r"температур|скорост|потер\w*\s+напор|правил\w*\s+(?:формирован|сумм)|"
                r"применим|пожар\w*\s+расход|расход\w*[^.!?]{0,50}пожар",
                text,
                re.I,
            )
        )
        and literal_contains(
            text + " " + finding.evidence, assertion.observations[0].fact.identifier
        )
        and not diagnostic_language(text + " " + finding.recommendation_draft)
        and (
            not re.search(
                r"отсутств|\bнет\b|не\s+(?:привед[её]н|представлен|указан)", text, re.I
            )
            or bool(re.search(r"в\s+(?:проверенн|представленн|доступн)\w*", text, re.I))
        )
        and set(re.findall(r"\d+(?:[.,]\d+)?", text.replace(",", ".")))
        <= {
            value
            for item in assertion.observations
            for value in re.findall(
                r"\d+(?:[.,]\d+)?", item.fact.evidence_text.replace(",", ".")
            )
        }
    )


def _audit_finding(finding, *, page_texts, stage):
    """Не считает категорию или решение модели доказательством инженерного противоречия."""
    finding = _bind_legacy_context(finding)
    assertion = read_assertion(finding.origin_assertions)
    if (
        assertion is not None
        and assertion.claim_type == "missing_justification"
        and literal_contains(finding.comment, assertion.observations[0].fact.identifier)
        and literal_contains(finding.comment, assertion.observations[0].fact.value_raw)
        and re.search(
            r"(?:отсутств|нет|не\s+(?:представлен|привед[её]н))[^.!?]{0,70}расч[её]т|расч[её]т[^.!?]{0,70}(?:отсутств|не\s+представлен)",
            finding.comment,
            re.I,
        )
        and all(
            bind_evidence(item, page_texts).status == "supported"
            for item in assertion.observations
        )
    ):
        first = assertion.observations[0].fact
        finding = replace(
            finding,
            comment=(
                f"Для {first.identifier} принято {first.property_name}: {first.value_raw} {first.unit_raw}. "
                "Уточнить расчёт и исходные условия, связывающие принятое решение с обоснованием "
                "в предоставленной части документации."
            ),
            recommendation_draft=(
                "Указать соответствующий расчёт; "
                "отсутствие обоснования во всём проекте не установлено."
            ),
            origin_assertions=(
                *finding.origin_assertions,
                {
                    "origin": "checked_scope_wording",
                    "checked_pages": sorted(page_texts),
                    "original": {
                        "comment": finding.comment,
                        "recommendation_draft": finding.recommendation_draft,
                    },
                },
            ),
        )
    if (
        assertion is not None
        and assertion.claim_type == "missing_justification"
        and assertion.property_key.endswith(":system_purpose")
        and re.search(
            r"^(?:уточнить|сверить|согласовать)\s+(?:назначение|наименование)\b",
            finding.comment,
            re.I,
        )
        and not re.search(
            r"расч[её]т|исходн\w*\s+услов|почему|обоснован|требован",
            finding.comment,
            re.I,
        )
    ):
        assertion = replace(assertion, claim_type="contradiction")
        finding = replace(
            finding,
            origin_assertions=(
                assertion.as_origin(),
                *(
                    {**origin, "origin": "engineering_assertion_history"}
                    if origin.get("origin") == "engineering_assertion"
                    else origin
                    for origin in finding.origin_assertions
                ),
            ),
        )
    implementation_text = diagnostic_language(
        finding.comment + " " + finding.recommendation_draft
    )
    from pdrd_analysis_service.domain.structural_evidence import (
        structural_counterevidence,
    )

    counterevidence = structural_counterevidence(
        finding.comment, finding.evidence, page_texts
    )
    if re.search(
        r"норматив\w*[^.!?]{0,60}(?:может\s+не\s+(?:покрывать|учитывать)|возможно\s+не\s+охватывает)",
        finding.comment,
        re.I,
    ):
        counterevidence = "normative_applicability_claim_not_established"
    if assertion is None and re.search(
        r"(?:во\s+вс[её]м\s+проекте|в\s+проекте)[^.!?]{0,70}"
        r"(?:отсутств\w*|нет|не\s+представлен\w*)[^.!?]{0,50}расч[её]т",
        finding.comment,
        re.I,
    ):
        counterevidence = "project_wide_calculation_absence_not_established"
    if counterevidence:
        return replace(
            finding,
            status="hypothesis",
            origin_assertions=(
                *finding.origin_assertions,
                {
                    "origin": AUDIT_ORIGIN,
                    "stage": stage,
                    "status": "counterevidence",
                    "disposition": "diagnostic",
                    "reason_code": counterevidence,
                    "original": {
                        "status": finding.status,
                        "comment": finding.comment,
                        "evidence": finding.evidence,
                        "recommendation_draft": finding.recommendation_draft,
                    },
                    "page_texts": {
                        str(page): text for page, text in page_texts.items()
                    },
                },
            ),
        )
    if assertion is None and not implementation_text:
        from pdrd_analysis_service.domain.project_dependencies import (
            normative_reference_continuations,
        )

        continuations = normative_reference_continuations(page_texts)
        incomplete_ref = re.search(
            r"неполн\w*\s+(?:нормативн\w*\s+)?ссылк|(?:не\s+указан|отсутствует|без)\s+номер|ссылк\w*[^.!?]{0,30}(?:обрыв|не\s+заверш)",
            finding.comment,
            re.I,
        )
        resolved = tuple(
            fact
            for _, fact in continuations
            if finding.page in fact.source_pages
            and literal_contains(
                finding.comment + " " + finding.evidence, fact.value_raw.split()[0]
            )
            and finding.evidence.strip()
            and any(
                literal_contains(part["text"], finding.evidence)
                or literal_contains(finding.evidence, part["text"])
                for part in fact.source_fragments
                if part["page"] == finding.page
            )
        )
        if incomplete_ref and resolved:
            origin = {
                "origin": AUDIT_ORIGIN,
                "stage": stage,
                "status": "counterevidence",
                "disposition": "diagnostic",
                "reason_code": "normative_reference_continues_on_next_page",
                "original": {
                    "comment": finding.comment,
                    "evidence": finding.evidence,
                    "status": finding.status,
                },
                "continuations": [asdict(fact) for fact in resolved],
                "page_texts": {
                    str(page): text
                    for page, text in page_texts.items()
                    if any(page in fact.source_pages for fact in resolved)
                },
            }
            return replace(
                finding,
                status="hypothesis",
                origin_assertions=(*finding.origin_assertions, origin),
            )
        pages = {
            finding.page,
            *(source.page for source in finding.document_context_basis_sources),
        }
        for origin in finding.origin_assertions:
            context = origin.get("context", {})
            if isinstance(context, dict) and isinstance(context.get("page"), int):
                pages.add(context["page"])
        if re.search(
            r"\b(?:СП|ГОСТ|СНиП)\b", finding.comment + " " + finding.evidence, re.I
        ):
            pages.update((finding.page - 1, finding.page + 1))
        return replace(
            finding,
            origin_assertions=(
                *(
                    origin
                    for origin in finding.origin_assertions
                    if origin.get("origin") != "engineering_evidence_context"
                ),
                {
                    "origin": "engineering_evidence_context",
                    "stage": stage,
                    "page_texts": {
                        str(page): text
                        for page, text in page_texts.items()
                        if page in pages
                    },
                },
            ),
        )
    # Программные доказательства и арифметика этапа 1 имеют собственную повторную проверку.
    if not implementation_text and (
        (assertion is not None and assertion.claim_type == "arithmetic")
        or any(
            origin.get("origin") == "programmatic_engineering_check"
            for origin in finding.origin_assertions
        )
        or any(
            origin.get("review_kind") == "table_arithmetic"
            for origin in finding.origin_assertions
        )
    ):
        return finding
    audit = (
        audit_assertion(assertion, page_texts=page_texts)
        if assertion is not None
        else EvidenceAudit(
            "unidentified",
            "diagnostic",
            "analysis_diagnostic_without_engineering_subject",
            (),
        )
    )
    missing_sources = _source_limits(finding)
    if audit.disposition == "question" and assertion is not None:
        anchored = tuple(
            item
            for item, binding in zip(
                assertion.observations, audit.bindings, strict=True
            )
            if binding.question_anchor
        )
        meaningful_original = _specific_original_question(finding, assertion)
        if (
            assertion.claim_type == "missing_justification" and not meaningful_original
        ) or (
            assertion.claim_type != "missing_justification"
            and len({(item.fact.value_raw, item.fact.unit_raw) for item in anchored})
            < 2
        ):
            audit = replace(
                audit,
                disposition="diagnostic",
                reason_code="engineering_question_basis_not_established",
            )
    if missing_sources and audit.disposition == "supported":
        audit = replace(
            audit,
            status="source_unavailable",
            disposition="question",
            reason_code="cited_source_unavailable",
        )
    comment, recommendation = finding.comment, finding.recommendation_draft
    evidence = finding.evidence
    status = finding.status
    if audit.disposition == "diagnostic":
        status = "hypothesis"
    elif audit.disposition == "question":
        # Не повышаем уже отложенную гипотезу и не возвращаем отвергнутую арифметику.
        if status != "hypothesis":
            status = "needs_review"
        if _specific_original_question(finding, assertion):
            comment, recommendation = finding.comment, finding.recommendation_draft
        else:
            comment, recommendation = _question(assertion, audit)
    elif audit.disposition == "supported":
        # Реальное расхождение остаётся, но технический пересказ заменяется его исходными сторонами.
        first = assertion.observations[0].fact
        values = "; ".join(
            dict.fromkeys(
                f"стр. {item.page}: {item.fact.value_raw} {item.fact.unit_raw}".strip()
                for item in assertion.observations
            )
        )
        comment = (
            f"Для объекта {first.identifier} различаются записи параметра «{first.property_name}»: {values}. "
            "Требуется определить корректное значение для сопоставленных условий."
        )
        recommendation = "Сопоставить исходные записи и привести документацию к согласованному решению."
        if audit.reason_code == "explicit_inventory_composition":
            declaration = next(
                item.fact
                for item in assertion.observations
                if item.fact.property_type == "inventory_count"
            )
            tags = tuple(
                dict.fromkeys(
                    item.fact.identifier
                    for item in assertion.observations
                    if item.fact.relation == "member_of"
                )
            )
            comment = (
                f"Для группы {declaration.identifier} заявлено {declaration.value_raw}, "
                f"в связанных материалах указаны {len(tags)} уникальных элементов: {', '.join(tags)}."
            )
            recommendation = "Сверить заявленное количество с перечнем той же группы и условиями учёта элементов."
        elif audit.reason_code == "explicit_sheet_sequence":
            comment = (
                "Обнаружено расхождение в последовательности печатных номеров "
                f"документа {first.identifier}: {values}."
            )
            recommendation = (
                "Проверить печатную последовательность по перечню листов "
                "с учётом раздельных частей документа."
            )
    if audit.disposition != "diagnostic":
        evidence = "; ".join(
            dict.fromkeys(
                f"стр. {item.page}: «{item.fact.evidence_text}»"
                for item, binding in zip(
                    assertion.observations, audit.bindings, strict=True
                )
                if binding.question_anchor
            )
        )
    previous = tuple(
        origin
        for origin in finding.origin_assertions
        if origin.get("origin") == AUDIT_ORIGIN
    )
    original = (
        previous[0]["original"]
        if previous
        else {
            "status": finding.status,
            "comment": finding.comment,
            "recommendation_draft": finding.recommendation_draft,
            "evidence": finding.evidence,
        }
    )
    relevant_pages = (
        {item.page for item in assertion.observations} if assertion else {finding.page}
    )
    origin = {
        "origin": AUDIT_ORIGIN,
        "stage": stage,
        **audit.as_dict(),
        "finding_id": finding.finding_id,
        "assertion_id": assertion.assertion_id if assertion else None,
        "missing_source_ids": missing_sources,
        "original": original,
        "page_texts": {
            str(page): text
            for page, text in page_texts.items()
            if page in relevant_pages
        },
    }
    trace_candidate(
        stage,
        finding.finding_id,
        page=finding.page,
        decision=audit.disposition,
        reason=audit.reason_code,
        observations=len(audit.bindings),
        missing_sources=len(missing_sources),
    )
    return replace(
        finding,
        status=status,
        comment=comment,
        recommendation_draft=recommendation,
        evidence=evidence,
        origin_assertions=(
            *(
                origin
                for origin in finding.origin_assertions
                if origin.get("origin") != AUDIT_ORIGIN
            ),
            *(
                {**origin, "origin": "engineering_evidence_audit_history"}
                for origin in previous
            ),
            origin,
        ),
    )


def finalize_evidence_candidate(finding: FindingDraft) -> FindingDraft:
    """Повторяет проверку сохранённого контекста, включая объединённые страницы двух кандидатов."""
    audits = tuple(
        origin
        for origin in finding.origin_assertions
        if origin.get("origin") in {AUDIT_ORIGIN, "engineering_evidence_context"}
    )
    if not audits:
        if diagnostic_language(finding.comment + " " + finding.recommendation_draft):
            return _audit_finding(finding, page_texts={}, stage="final_evidence_audit")
        return finding
    page_texts = {
        int(page): text
        for audit in audits
        for page, text in audit.get("page_texts", {}).items()
        if isinstance(text, str) and str(page).isdigit()
    }
    return _audit_finding(finding, page_texts=page_texts, stage="final_evidence_audit")


def evidence_audit(finding):
    """Возвращает последний результат, сохраняя предыдущие решения в истории."""
    return next(
        (
            origin
            for origin in reversed(finding.origin_assertions)
            if origin.get("origin") == AUDIT_ORIGIN
        ),
        None,
    )


def protected_evidence_question(finding):
    """Не требует генерации для уже сформулированного предметного вопроса по исходным фактам."""
    audit = evidence_audit(finding)
    return (
        finding.status == "needs_review"
        and audit is not None
        and audit.get("disposition") == "question"
        and not diagnostic_language(
            finding.comment + " " + finding.recommendation_draft
        )
    )


def diagnostic_record(finding):
    """Сохраняет исходный кандидат целиком в метриках этапа, отдельно от пользовательских findings."""
    audit = evidence_audit(finding)
    if audit is None or audit["disposition"] != "diagnostic":
        return None
    return {
        "finding_id": finding.finding_id,
        "page": finding.page,
        "reason": audit["reason_code"],
        "reason_code": audit["reason_code"],
        "disposition": "internal_diagnostic",
        "candidate": asdict(finding),
    }
