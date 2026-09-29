# services/experience-service/src/pdrd_experience_service/application/use_cases/export_artifact_dataset.py

"""Переносимый ZIP фиксированной версии без чтения текущих полей каталога.

Это подготовка данных, а не запуск обучения или готовые веса. Разметка
положительных и отрицательных примеров сохраняется явно вместе с происхождением.
"""

import hashlib
import io
import json
import re
import struct
import zipfile
from dataclasses import dataclass
from uuid import UUID

from pdrd_experience_service.application.ports.artifacts import ArtifactRepository
from pdrd_experience_service.application.ports.catalog import CropStore
from pdrd_experience_service.core.observability import log_execution_time
from pdrd_experience_service.domain.artifacts import manifest_hash
from pdrd_experience_service.domain.catalog import Crop
from pdrd_experience_service.domain.review import ReviewConflictError, ReviewError


def _json(value: dict) -> bytes:
    """Одинаковый снимок формирует одинаковый UTF-8 без зависящих от ОС переводов."""
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def _verified_crop(value: dict, content: bytes) -> Crop:
    """Архив не публикует повреждённые байты даже при ошибке реализации порта."""
    try:
        crop = Crop(**value)
        if (
            not re.fullmatch(r"[a-f0-9]{64}", crop.sha256)
            or not 33 <= len(content) <= 12_000_000
            or content[:8] != b"\x89PNG\r\n\x1a\n"
            or content[12:16] != b"IHDR"
            or struct.unpack(">II", content[16:24]) != (crop.width, crop.height)
            or not 1 <= crop.width <= 2000
            or not 1 <= crop.height <= 2000
            or hashlib.sha256(content).hexdigest() != crop.sha256
        ):
            raise ValueError("Несоответствующее изображение.")
        return crop
    except (TypeError, ValueError, struct.error) as error:
        raise ReviewError("Изображение фиксированного набора повреждено.") from error


@dataclass(frozen=True, slots=True)
class ExportArtifactDataset:
    """Данные берутся только из immutable members и адресованного SHA PNG-store."""

    repository: ArtifactRepository
    crops: CropStore
    max_examples: int = 1000
    max_bytes: int = 100_000_000

    @log_execution_time(operation="experience_version_export")
    async def execute(self, version_id: UUID) -> bytes:
        """Проверяет manifest и повторно запрещает удалённую во время выгрузки версию."""
        version = await self.repository.get(version_id)
        if version is None or version.deleted:
            raise LookupError("Версия набора не найдена.")
        if not 1 <= len(version.members) <= self.max_examples:
            raise ReviewError("Выгрузка допускает от 1 до 1000 примеров.")
        if (
            manifest_hash(
                kind=version.kind,
                model=version.model,
                section_id=version.section_id,
                members=version.members,
            )
            != version.manifest_sha256
        ):
            raise ReviewError("Состав версии не соответствует сохранённому manifest.")
        buffer = io.BytesIO()
        files, rows, total = [], [], 0
        ids = set()

        def write(archive, name, content):
            """Ограничивает суммарный объём и фиксирует ZIP-метаданные для повторяемости."""
            nonlocal total
            total += len(content)
            if total > self.max_bytes:
                raise ReviewError(
                    "Фиксированный набор превышает предел выгрузки 100 МБ."
                )
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)

        with zipfile.ZipFile(buffer, "w") as archive:
            for member in version.members:
                try:
                    example_id = UUID(member["example_id"])
                    crop_values = member["crops"]
                    if example_id in ids or not isinstance(crop_values, list):
                        raise ValueError("Некорректный состав набора.")
                    ids.add(example_id)
                except (KeyError, TypeError, ValueError) as error:
                    raise ReviewError("Повреждён снимок примера в версии.") from error
                crop_files = []
                for ordinal, value in enumerate(crop_values):
                    try:
                        content = await self.crops.read(Crop(**value))
                    except (TypeError, ValueError) as error:
                        raise ReviewError(
                            "Повреждены сведения об изображении набора."
                        ) from error
                    crop = _verified_crop(value, content)
                    path = f"crops/{example_id}/{ordinal}.png"
                    write(archive, path, content)
                    file = {
                        "path": path,
                        "sha256": crop.sha256,
                        "width": crop.width,
                        "height": crop.height,
                    }
                    files.append(file)
                    crop_files.append(file)
                rows.append(
                    {
                        **member,
                        "geometry_status": "recorded"
                        if "geometry" in member
                        else "not_recorded_in_version",
                        "crop_files": crop_files,
                    }
                )
            examples = b"\n".join(_json(row) for row in rows) + b"\n"
            write(archive, "examples.jsonl", examples)
            manifest = {
                "format": "pdrd-experience-artifact-dataset",
                "schema_version": 1,
                "purpose": "dataset_preparation",
                "artifact_id": str(version.id),
                "artifact_kind": version.kind,
                "artifact_name": version.name,
                "artifact_revision": version.revision,
                "artifact_created_at": version.created_at.isoformat(),
                "artifact_created_by": version.created_by,
                "artifact_manifest_sha256": version.manifest_sha256,
                "base_model_contract": version.model,
                "section_id": version.section_id,
                "section_title": version.section_title,
                "member_count": len(rows),
                "missing_geometry_count": sum(
                    row["geometry_status"] != "recorded" for row in rows
                ),
                "examples_file": "examples.jsonl",
                "examples_sha256": hashlib.sha256(examples).hexdigest(),
                "crop_files": files,
                "training_started": False,
            }
            write(archive, "manifest.json", _json(manifest))
        current = await self.repository.get(version_id)
        if current != version or current.deleted:
            raise ReviewConflictError(
                "Версия изменена во время выгрузки; повторите операцию."
            )
        return buffer.getvalue()
