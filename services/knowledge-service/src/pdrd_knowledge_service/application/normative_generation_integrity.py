# services/knowledge-service/src/pdrd_knowledge_service/application/normative_generation_integrity.py

"""Проверка записанных атомов перед публикацией и административным восстановлением."""

import hashlib
import json
from uuid import UUID

from pdrd_knowledge_service.domain.normative_requirements import NormativeRequirement


class NormativeGenerationIntegrityError(ValueError):
    """Число строк, происхождение или содержимое поколения не подтверждены."""


def update_digest(digest, row: dict) -> None:
    """Хеширует поток JSON-строк в устойчивом порядке ordinal без накопления всего каталога."""
    digest.update(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    digest.update(b"\n")


def validate_row(row, generation, pages=None):
    """Сверяет поля и буквальные фрагменты; близость текста не считается доказательством."""
    try:
        if set(row) != set(NormativeRequirement.__dataclass_fields__):
            raise ValueError("Неверный состав полей атома.")
        for key in (
            "applicability_conditions",
            "exceptions",
            "required_parameters",
            "normative_dependencies",
            "context",
            "limitations",
        ):
            if not isinstance(row[key], (list, tuple)) or not all(
                isinstance(value, str) for value in row[key]
            ):
                raise ValueError("Повреждён массив условий или ограничений атома.")
        UUID(row["point_id"])
        if (
            row["requirement_id"] != "N-R-" + row["point_id"]
            or row["document_id"] != generation["document_id"]
            or row["source_sha256"] != generation["source_sha256"]
            or type(row["page"]) is not int
            or row["page"] not in generation["manifest"]["pages"]
            or not row["source_text"].strip()
            or not row["text"].strip()
            or not row["source_fragments"]
        ):
            raise ValueError("Не подтверждено происхождение атома.")
        for fragment in row["source_fragments"]:
            if (
                set(fragment) != {"page", "start", "end", "text"}
                or fragment["page"] not in generation["manifest"]["pages"]
                or not isinstance(fragment["start"], int)
                or not isinstance(fragment["end"], int)
                or not 0 <= fragment["start"] <= fragment["end"]
                or len(fragment["text"]) != fragment["end"] - fragment["start"]
            ):
                raise ValueError("Неверный фрагмент исходной страницы.")
            if (
                pages is not None
                and pages[fragment["page"]][fragment["start"] : fragment["end"]]
                != fragment["text"]
            ):
                raise ValueError("Цитата отличается от исходной страницы.")
        json.dumps(row, ensure_ascii=False, allow_nan=False)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise NormativeGenerationIntegrityError(str(error)) from error


async def validate_generation(repository, generation, *, pages=None) -> dict:
    """Читает реальные строки порциями; счётчик в JSON не заменяет проверку содержимого."""
    manifest = generation.get("manifest", {})
    if not isinstance(manifest, dict):
        raise NormativeGenerationIntegrityError("Повреждён JSON manifest поколения.")
    expected = generation.get("count", 0)
    if (
        type(expected) is not int
        or expected <= 0
        or not manifest.get("requirements_digest")
        or not isinstance(manifest.get("pages"), list)
        or not manifest["pages"]
        or len(manifest["pages"]) != len(set(manifest["pages"]))
        or manifest.get("extractor_version") != generation["extractor_version"]
        or manifest.get("source_sha256") != generation["source_sha256"]
    ):
        raise NormativeGenerationIntegrityError(
            "Отсутствует проверяемый manifest поколения."
        )
    digest, seen, offset = hashlib.sha256(), set(), 0
    while rows := await repository.list_requirements(
        generation["generation_id"], offset, 200
    ):
        for row in rows:
            validate_row(row, generation, pages)
            if row["requirement_id"] in seen:
                raise NormativeGenerationIntegrityError("Повтор идентификатора атома.")
            seen.add(row["requirement_id"])
            update_digest(digest, row)
        offset += len(rows)
    actual = await repository.count_requirements(generation["generation_id"])
    if (
        actual != offset
        or offset != expected
        or digest.hexdigest() != manifest["requirements_digest"]
    ):
        raise NormativeGenerationIntegrityError(
            "Счётчик или digest записанных атомов не совпадает с manifest."
        )
    return {
        "actual_count": actual,
        "requirements_digest": digest.hexdigest(),
        "integrity": "verified",
    }
