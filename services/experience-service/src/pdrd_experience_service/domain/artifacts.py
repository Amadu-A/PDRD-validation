# services/experience-service/src/pdrd_experience_service/domain/artifacts.py

"""Версии векторных баз и наборов дообучения с неизменяемым составом примеров.

Название редактируется отдельно. Снимок состава не меняется после запуска,
даже если инженер позже исправил или удалил замечание в каталоге.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pdrd_experience_service.domain.review import ReviewError


@dataclass(frozen=True, slots=True)
class ArtifactVersion:
    """Векторная версия готовится worker; набор для дообучения ещё не является весами."""

    id: UUID
    kind: str
    name: str
    model: str
    section_id: str
    section_title: str
    members: tuple[dict, ...]
    manifest_sha256: str
    created_at: datetime
    created_by: str
    revision: int = 0
    status: str = "queued"
    collection: str = ""
    embedding_identity: str = ""
    dimension: int = 0
    error: str = ""
    deleted: bool = False
    quality_approved: bool = False
    weights_sha256: str = ""
    quality_report: dict | None = None


def manifest_hash(
    *, kind: str, model: str, section_id: str, members: tuple[dict, ...]
) -> str:
    """Отчёт качества и артефакт связываются с точным составом и редакциями."""
    content = json.dumps(
        {
            "schema_version": 1,
            "kind": kind,
            "model": model,
            "section_id": section_id,
            "members": members,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_name(name: str) -> str:
    """Имя отображается пользователю и не используется как путь или имя коллекции."""
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
        raise ReviewError("Название версии должно содержать от 1 до 200 символов.")
    return name.strip()
