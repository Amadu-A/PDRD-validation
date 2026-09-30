# services/knowledge-service/src/pdrd_knowledge_service/domain/experience_quality.py

"""Оценка E на отложенных документах: retrieval и сравнение решений с базовой моделью.

Синтетические тесты проверяют расчёт, но не являются разрешением рабочего E.
Для отчёта нужны результаты отдельного эксперимента baseline/with_experience.
"""

import re
from collections.abc import Sequence
from uuid import UUID


def validate_evaluation_cases(
    cases: Sequence[dict], finding_cases: Sequence[dict]
) -> None:
    """Проверяет разметку и запрещает повторы, искусственно увеличивающие выборку."""
    if not 1 <= len(cases) <= 1000 or not 1 <= len(finding_cases) <= 1000:
        raise ValueError("Некорректный размер набора оценки E.")
    queries, documents = set(), set()
    hashes = {}
    try:
        for case in (*cases, *finding_cases):
            document = str(UUID(case["document_id"]))
            digest = case["source_sha256"]
            if not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("Нужен SHA-256 исходного документа оценки.")
            if document in hashes and hashes[document] != digest:
                raise ValueError("Один документ оценки имеет разные исходные SHA-256.")
            hashes[document] = digest
        for case in cases:
            document = str(UUID(case["document_id"]))
            query = case["query"]
            if not isinstance(query, str) or not 1 <= len(query.strip()) <= 10000:
                raise ValueError("Некорректный запрос оценки.")
            key = (document, query.strip())
            if key in queries:
                raise ValueError("Повтор запроса оценки в одном документе.")
            queries.add(key)
            relevant = case["relevant_example_ids"]
            forbidden = case.get("forbidden_example_ids", [])
            for values in (relevant, forbidden):
                if not isinstance(values, list) or len(values) > 100:
                    raise ValueError("Некорректные метки retrieval.")
                canonical = [str(UUID(value)) for value in values]
                if canonical != values or len(set(values)) != len(values):
                    raise ValueError("Повтор или некорректный идентификатор примера.")
            if not relevant or set(relevant) & set(forbidden):
                raise ValueError("Неполная или противоречивая разметка retrieval.")
        for case in finding_cases:
            document = str(UUID(case["document_id"]))
            if document in documents:
                raise ValueError("Повтор документа в парном сравнении анализа.")
            documents.add(document)
            for field in ("expected", "baseline", "with_experience"):
                labels = case[field]
                if (
                    not isinstance(labels, list)
                    or len(labels) > 1000
                    or any(
                        not isinstance(label, str) or not 1 <= len(label) <= 500
                        for label in labels
                    )
                    or len(set(labels)) != len(labels)
                ):
                    raise ValueError("Некорректные метки замечаний.")
    except (AttributeError, KeyError, TypeError) as error:
        raise ValueError("Некорректная структура набора оценки E.") from error


def finding_metrics(cases: Sequence[dict], field: str) -> dict:
    """Считает precision, recall и ложные срабатывания по размеченным идентификаторам."""
    true_positive = false_positive = false_negative = 0
    for case in cases:
        expected, actual = set(case["expected"]), set(case[field])
        true_positive += len(expected & actual)
        false_positive += len(actual - expected)
        false_negative += len(expected - actual)
    return {
        "precision": true_positive / max(1, true_positive + false_positive),
        "recall": true_positive / max(1, true_positive + false_negative),
        "false_positives": false_positive,
    }


def quality_report(
    *,
    cases: Sequence[dict],
    retrieved: Sequence[Sequence[str]],
    finding_cases: Sequence[dict],
    indexed_documents: set[str],
    indexed_source_hashes: set[str] | None = None,
) -> dict:
    """Не допускает утечку документов, загрязнение и ухудшение качества решений."""
    validate_evaluation_cases(cases, finding_cases)
    if len(cases) != len(retrieved) or not cases or not finding_cases:
        raise ValueError("Для оценки E нужны retrieval и парные результаты анализа.")
    documents = {str(UUID(case["document_id"])) for case in (*cases, *finding_cases)}
    if documents & indexed_documents:
        raise ValueError("Документы оценки уже присутствуют в индексе E.")
    hashes = {case["source_sha256"] for case in (*cases, *finding_cases)}
    if hashes & (indexed_source_hashes or set()):
        raise ValueError(
            "Исходный PDF оценки уже присутствует в E под другим document_id."
        )
    if len({case["source_sha256"] for case in finding_cases}) != len(finding_cases):
        raise ValueError("Повтор исходного документа в парном сравнении анализа.")
    if any(not case["relevant_example_ids"] for case in cases):
        raise ValueError(
            "Каждый retrieval-запрос требует известные релевантные примеры."
        )
    recall = sum(
        len(set(result) & set(case["relevant_example_ids"]))
        / len(set(case["relevant_example_ids"]))
        for case, result in zip(cases, retrieved, strict=True)
    ) / len(cases)
    contamination = sum(
        len(set(result) & set(case.get("forbidden_example_ids", ())))
        for case, result in zip(cases, retrieved, strict=True)
    )
    baseline, augmented = (
        finding_metrics(finding_cases, "baseline"),
        finding_metrics(finding_cases, "with_experience"),
    )
    enough = (
        len(cases) >= 20 and len({case["document_id"] for case in finding_cases}) >= 5
    )
    improved = (
        augmented["precision"] > baseline["precision"]
        or augmented["recall"] > baseline["recall"]
    )
    approved = (
        enough
        and recall >= 0.8
        and contamination == 0
        and improved
        and (
            augmented["precision"] >= baseline["precision"]
            and augmented["recall"] >= baseline["recall"]
            and augmented["false_positives"] <= baseline["false_positives"]
        )
    )
    return {
        "report_schema_version": 1,
        "approved": approved,
        "retrieval_cases": len(cases),
        "finding_documents": len({case["document_id"] for case in finding_cases}),
        "recall_at_k": recall,
        "forbidden_hits": contamination,
        "baseline": baseline,
        "with_experience": augmented,
    }
