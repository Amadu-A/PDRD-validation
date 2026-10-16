# services/analysis-service/src/pdrd_analysis_service/application/evidence_audit.py

"""Применение проверки доказательств в существующих этапах и origin_assertions."""

from __future__ import annotations

import re
from dataclasses import asdict, replace

from pdrd_analysis_service.application.candidate_trace import trace_candidate
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    document_fact_from_mapping,
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
    if types and not types <= {"contradiction", "missing_justification"}:
        return finding
    if not types and not re.search(
        r"различ|расхожд|несоответств|противореч|обоснован", finding.comment, re.I
    ):
        return finding
    prop = candidate_property(finding.comment + " " + finding.evidence)
    patterns = {
        "Толщина стенки": r"толщин",
        "Диаметр": r"диаметр|типоразмер",
        "Длина": r"длин|протяж",
        "Назначение системы": r"назначени|наименовани",
        "Расход воды": r"расход",
        "Напор": r"напор",
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
            if fact is None or not re.search(patterns[prop], fact.property_name, re.I):
                continue
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
    assertion = assertion_from_facts(
        tuple(observations), claim_type=claim_type, include_related=True
    )
    if assertion is None:
        return finding
    return replace(
        finding, origin_assertions=(*finding.origin_assertions, assertion.as_origin())
    )


def audit_findings_evidence(findings, *, page_texts: dict[int, str], stage: str):
    """Проверяет все переданные кандидаты без изменения источников, страниц и областей."""
    return tuple(
        _audit_finding(finding, page_texts=page_texts, stage=stage)
        for finding in findings
    )


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
    """Формулирует вопрос только по подтверждённым исходным записям, без выдуманной второй стороны."""
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
    if assertion.claim_type == "missing_justification":
        return (
            f"Уточнить обоснование параметра «{prop}» объекта {subject}: {values}.",
            "Указать исходные условия и расчётное либо документальное основание принятого решения.",
        )
    return (
        f"Сверить параметр «{prop}» объекта {subject}: {values}. "
        "Подтвердить, к какому участку и условиям относится принятое значение.",
        "Сопоставить указанные записи для того же объекта, участка и режима; "
        "подтвердить принятое решение по исходной документации.",
    )


def _specific_original_question(finding, assertion):
    """Сохраняет предметный вопрос о недостающей предпосылке, не подменяя его вопросом о самом числе."""
    text = finding.comment
    return (
        assertion.claim_type == "missing_justification"
        and bool(
            re.match(
                r"^(?:уточнить|указать|подтвердить|обосновать|как|где|чем|какая|какой)\b",
                text,
                re.I,
            )
        )
        and literal_contains(text, assertion.observations[0].fact.identifier)
        and not diagnostic_language(text + " " + finding.recommendation_draft)
        and not re.search(
            r"отсутств|\bнет\b|не\s+(?:привед[её]н|представлен|указан)", text, re.I
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
    implementation_text = diagnostic_language(
        finding.comment + " " + finding.recommendation_draft
    )
    if assertion is None and not implementation_text:
        return finding
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
        comment, recommendation = _question(assertion, audit)
        if _specific_original_question(finding, assertion):
            comment, recommendation = finding.comment, finding.recommendation_draft
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


def finalize_evidence_candidate(finding):
    """Повторяет проверку сохранённого контекста, включая объединённые страницы двух кандидатов."""
    audits = tuple(
        origin
        for origin in finding.origin_assertions
        if origin.get("origin") == AUDIT_ORIGIN
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
