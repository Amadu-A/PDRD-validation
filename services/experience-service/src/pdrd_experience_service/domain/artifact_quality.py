# services/experience-service/src/pdrd_experience_service/domain/artifact_quality.py

"""Допуск точной векторной версии по независимому отчёту, без обучения в HTTP.

Отчёт подписывается доверенным контекстом инженера и сохраняется в аудите.
Синтетический тест проверяет правило допуска, но не заменяет реальную оценку.
"""

import json
import math
import re
from datetime import UTC, datetime, timedelta

from pdrd_experience_service.domain.artifacts import ArtifactVersion, manifest_hash
from pdrd_experience_service.domain.review import ReviewError


def validate_quality_report(report: dict, version: ArtifactVersion) -> bool:
    """Проверяет привязку, независимость документов и пересчитывает решение допуска."""
    try:
        if version.kind != "vector" or version.status != "ready" or version.deleted:
            raise ValueError("Отчёт принимается только для готовой векторной версии.")
        if (
            not isinstance(report, dict)
            or len(
                json.dumps(report, ensure_ascii=False, allow_nan=False).encode("utf-8")
            )
            > 65536
        ):
            raise ValueError("Отчёт должен быть JSON-объектом размером до 64 КиБ.")
        bindings = {
            "version_id": str(version.id),
            "manifest_sha256": version.manifest_sha256,
            "model": version.model,
            "section_id": version.section_id,
            "embedding_identity": version.embedding_identity,
            "collection": version.collection,
        }
        if any(
            report.get(key) != value or not value for key, value in bindings.items()
        ):
            raise ValueError("Отчёт относится к другому составу, разделу или индексу.")
        if version.manifest_sha256 != manifest_hash(
            kind=version.kind,
            model=version.model,
            section_id=version.section_id,
            members=version.members,
        ):
            raise ValueError("Состав версии не соответствует её manifest SHA-256.")
        if (
            type(report["report_schema_version"]) is not int
            or report["report_schema_version"] != 1
            or type(report["dimension"]) is not int
            or report["dimension"] != version.dimension
            or version.dimension < 1
            or type(report["top_k"]) is not int
            or not 1 <= report["top_k"] <= 100
            or type(report["approved"]) is not bool
        ):
            raise ValueError("Некорректная схема или параметры поиска в отчёте.")
        for key in ("min_score", "recall_at_k"):
            value = report[key]
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError("Некорректная метрика поиска.")
        for key in ("retrieval_cases", "finding_documents", "forbidden_hits"):
            if type(report[key]) is not int or not 0 <= report[key] <= 1000000:
                raise ValueError("Некорректное число проверок или ложных совпадений.")
        if not re.fullmatch(r"[a-f0-9]{64}", report["dataset_sha256"]):
            raise ValueError("Укажите SHA-256 независимого проверочного набора.")
        hashes = report["evaluation_document_sha256"]
        if (
            not isinstance(hashes, list)
            or not 1 <= len(hashes) <= 2000
            or any(
                not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                for value in hashes
            )
            or len(set(hashes)) != len(hashes)
            or len(hashes) < report["finding_documents"]
            or set(hashes) & {item["source_sha256"] for item in version.members}
        ):
            raise ValueError(
                "Документы оценки повторяются или уже входят в Experience."
            )
        evaluated = datetime.fromisoformat(report["evaluated_at"])
        if (
            evaluated.utcoffset() != timedelta(0)
            or evaluated < version.created_at
            or evaluated > datetime.now(UTC) + timedelta(minutes=5)
        ):
            raise ValueError("Некорректная дата независимой оценки; требуется UTC.")
        baseline, augmented = report["baseline"], report["with_experience"]
        for metrics in (baseline, augmented):
            for key in ("precision", "recall"):
                value = metrics[key]
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not 0 <= value <= 1
                ):
                    raise ValueError("Некорректные метрики анализа.")
            if (
                type(metrics["false_positives"]) is not int
                or metrics["false_positives"] < 0
            ):
                raise ValueError("Некорректное число ложных замечаний.")
        approved = (
            report["retrieval_cases"] >= 20
            and report["finding_documents"] >= 5
            and report["recall_at_k"] >= 0.8
            and report["forbidden_hits"] == 0
            and augmented["precision"] >= baseline["precision"]
            and augmented["recall"] >= baseline["recall"]
            and augmented["false_positives"] <= baseline["false_positives"]
            and (
                augmented["precision"] > baseline["precision"]
                or augmented["recall"] > baseline["recall"]
            )
        )
        if report["approved"] != approved:
            raise ValueError("Решение approved не соответствует метрикам отчёта.")
        return approved
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ReviewError(f"Отчёт качества не принят: {error}") from error
