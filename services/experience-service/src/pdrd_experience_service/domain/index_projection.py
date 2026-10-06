# services/experience-service/src/pdrd_experience_service/domain/index_projection.py

"""Доверенная проекция каталога для E: актуальность, разметка и версия содержимого.

Не переносит управление каталогом в Knowledge и не делает Experience нормативом.
Отрицательная формулировка выбирается только по явной разметке инженера.
"""

import hashlib
import json

from pdrd_experience_service.domain.catalog import CatalogEntry


def exclusion_reason(entry: CatalogEntry) -> str:
    """Одна проверка допуска для worker и понятных причин ручного отбора."""
    example, source = entry.example, entry.example.source
    if not entry.active:
        return "Пример удалён, неактивен или его источник изменён."
    if source.decision.value == "pending":
        return "Решение инженера ещё не принято."
    if not source.issue_regions:
        return "Нет пригодной исходной или принятой области; запись остаётся текстовой."
    if len(example.crops) != len(source.issue_regions):
        return "Изображения областей не подготовлены."
    if example.tag == "gold" and source.decision.value != "accepted":
        return "Отклонённый Gold сохранён в каталоге, но не размечен для обучения."
    if example.learning_use not in {"positive", "negative"}:
        return "Для Edited + Rejected нужны причина отказа и объект отрицательного примера."
    if (example.learning_use == "positive" and source.decision.value != "accepted") or (
        example.learning_use == "negative" and source.decision.value != "rejected"
    ):
        return "Назначение обучения не соответствует решению инженера."
    if (
        example.learning_use == "negative"
        and example.tag != "bad"
        and not (
            example.rejection_reason
            and example.negative_target in {"original", "revised", "both"}
        )
    ):
        return "Не указан объект отрицательного примера и причина отказа."
    return ""


def index_projection(entry: CatalogEntry) -> dict | None:
    """Отбирает актуальный пример с crop и однозначным назначением обучения."""
    example, source = entry.example, entry.example.source
    if exclusion_reason(entry):
        return None
    if example.learning_use == "positive":
        texts = [{"target": "revised", "text": example.text}]
    elif example.tag == "bad":
        texts = [{"target": "original", "text": source.original_text}]
    else:
        texts = []
        if example.negative_target in {"original", "both"}:
            texts.append({"target": "original", "text": source.original_text})
        if example.negative_target in {"revised", "both"}:
            texts.append({"target": "revised", "text": example.text})
        if not example.rejection_reason or not texts:
            return None
    payload = {
        "schema_version": 1,
        "example_id": str(example.id),
        "example_revision": example.revision,
        "job_id": str(source.job_id),
        "document_id": str(source.document_id),
        "finding_id": source.finding_id,
        "approved_revision": source.approved_revision,
        "source_sha256": source.source_sha256,
        "source_filename": source.source_filename,
        "page_number": source.page_number,
        "tag": example.tag,
        "decision": source.decision.value,
        "learning_use": example.learning_use,
        "negative_target": example.negative_target,
        "rejection_reason": example.rejection_reason,
        "original_text": source.original_text,
        "text": example.text,
        "normative_basis": example.normative_basis,
        "normative_reference": example.normative_reference,
        "section_id": example.section_id,
        "section_title": example.section_title,
        "texts": texts,
        "crops": [
            {"sha256": crop.sha256, "width": crop.width, "height": crop.height}
            for crop in example.crops
        ],
    }
    if source.reason_category is not None:
        payload.update(reason_category=source.reason_category, comment=source.comment)
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return {**payload, "fingerprint": hashlib.sha256(canonical.encode()).hexdigest()}
