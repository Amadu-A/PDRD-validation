# services/knowledge-service/src/pdrd_knowledge_service/domain/experience_admission.py

"""Допуск рабочего E связан с составом, моделью и независимой оценкой версии.

Метрики отчёта не заменяют его серверное утверждение. Knowledge повторно проверяет
контракт владельца и исключает подмену параметров либо утечку документов оценки.
"""

import math
import re
from datetime import datetime, timedelta


def require_quality_metrics(
    report: dict, *, identity: str, collection: str, top_k: int, min_score: float
) -> None:
    """Проверяет конечные метрики, объём выборки и неизменные параметры retrieval."""
    if (
        type(report["report_schema_version"]) is not int
        or report["report_schema_version"] != 1
        or report["approved"] is not True
        or report["embedding_identity"] != identity
        or report["collection"] != collection
        or type(report["top_k"]) is not int
        or report["top_k"] != top_k
        or type(report["min_score"]) not in (int, float)
        or report["min_score"] != min_score
    ):
        raise ValueError("Оценка E не одобрена для текущего индекса.")
    for count in ("retrieval_cases", "finding_documents", "forbidden_hits"):
        if type(report[count]) is not int or report[count] < 0:
            raise ValueError("Некорректный объём оценки E.")
    for value in (report["recall_at_k"], report["min_score"]):
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError("Некорректная метрика E.")
    if (
        report["retrieval_cases"] < 20
        or report["finding_documents"] < 5
        or report["recall_at_k"] < 0.8
        or report["forbidden_hits"] != 0
    ):
        raise ValueError("Недостаточная оценка E.")
    baseline, augmented = report["baseline"], report["with_experience"]
    for metrics in (baseline, augmented):
        for name in ("precision", "recall"):
            value = metrics[name]
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError("Некорректная метрика анализа.")
        if (
            type(metrics["false_positives"]) is not int
            or metrics["false_positives"] < 0
        ):
            raise ValueError("Некорректное число ложных замечаний.")
    if (
        augmented["precision"] < baseline["precision"]
        or augmented["recall"] < baseline["recall"]
        or augmented["false_positives"] > baseline["false_positives"]
    ):
        raise ValueError("Оценка E ухудшила качество.")
    if not (
        augmented["precision"] > baseline["precision"]
        or augmented["recall"] > baseline["recall"]
    ):
        raise ValueError("Оценка E не показала улучшения.")
    if not re.fullmatch(r"[a-f0-9]{64}", report["dataset_sha256"]):
        raise ValueError("Отчёт E не привязан к датасету.")


def require_version_quality(
    report: dict,
    *,
    version_id: str,
    model: str,
    manifest_sha256: str,
    section_id: str,
    identity: str,
    dimension: int,
    collection: str,
    top_k: int,
    min_score: float,
    source_hashes: set[str],
) -> None:
    """Разрешает только точную оценённую версию без пересечения документов с индексом."""
    require_quality_metrics(
        report,
        identity=identity,
        collection=collection,
        top_k=top_k,
        min_score=min_score,
    )
    if (
        report["version_id"] != version_id
        or report["model"] != model
        or report["manifest_sha256"] != manifest_sha256
        or report["section_id"] != section_id
        or type(report["dimension"]) is not int
        or report["dimension"] != dimension
    ):
        raise ValueError("Отчёт относится к другой версии или модели.")
    hashes = report["evaluation_document_sha256"]
    if (
        not isinstance(hashes, list)
        or not 5 <= len(hashes) <= 2000
        or any(
            not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
            for value in hashes
        )
        or len(set(hashes)) != len(hashes)
        or len(hashes) < report["finding_documents"]
        or set(hashes) & source_hashes
    ):
        raise ValueError("Набор оценки не является независимым от выбранной версии.")
    evaluated = datetime.fromisoformat(report["evaluated_at"].replace("Z", "+00:00"))
    if evaluated.utcoffset() != timedelta(0):
        raise ValueError("Время оценки должно содержать часовой пояс UTC.")
