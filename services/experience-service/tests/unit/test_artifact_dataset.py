# services/experience-service/tests/unit/test_artifact_dataset.py

"""Фиксированный экспорт не подменяет тексты, разметку и изображения новым каталогом."""

import base64
import hashlib
import io
import json
import zipfile
from dataclasses import replace
from uuid import uuid4

import pytest
from pdrd_experience_service.application.use_cases.export_artifact_dataset import (
    ExportArtifactDataset,
)
from pdrd_experience_service.domain.artifact_projection import (
    artifact_member_projection,
)
from pdrd_experience_service.domain.artifacts import ArtifactVersion, manifest_hash
from pdrd_experience_service.domain.catalog import CatalogEntry, Crop
from pdrd_experience_service.domain.index_projection import index_projection
from pdrd_experience_service.domain.review import (
    Decision,
    ProposedRegion,
    Rectangle,
    ReviewConflictError,
    ReviewError,
)

from .test_catalog import AT, example

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9tEAAAAASUVORK5CYII="
)


class Registry:
    """Порт только версии: экспорт вообще не получает ссылку на текущий каталог."""

    def __init__(self, version, change=None):
        """Второе чтение может наблюдать конкурентное изменение версии."""
        self.version, self.change, self.calls = version, change, 0

    async def get(self, version_id):
        """Возвращает исходный снимок, затем при необходимости изменённый."""
        self.calls += 1
        return (
            self.change if self.calls > 1 and self.change is not None else self.version
        )


class Images:
    """Наблюдаемые байты PNG без HTTP и файловой системы."""

    def __init__(self, content=PNG):
        """Подмена байтов проверяет целостность отдельно от реализации CropStore."""
        self.content, self.calls = content, []

    async def read(self, crop):
        """Сохраняет SHA снимка, по которому запрошен immutable crop."""
        self.calls.append(crop)
        return self.content


def prepared(*, kind="fine_tune", bad=False):
    """Воспроизводит разные исходные и отображаемые области отклонённой находки."""
    record = example(
        tag="bad" if bad else "wise",
        decision=Decision.REJECTED if bad else Decision.ACCEPTED,
    )
    original = Rectangle(10, 20, 100, 200)
    record = replace(
        record,
        text=record.source.original_text if bad else record.text,
        source=replace(
            record.source,
            text=record.source.original_text if bad else record.source.text,
            proposed_regions=(
                ProposedRegion(original, "analysis_vlm", 0.9, "visualization"),
            ),
            display_regions=(Rectangle(50, 60, 120, 220),),
            area_source="vlm_original" if bad else "engineer_confirmed",
        ),
        crops=(Crop(hashlib.sha256(PNG).hexdigest(), 1, 1),),
        section_id="СП 1:6",
        section_title="СП 1, раздел 6",
    )
    member = artifact_member_projection(CatalogEntry(record, True), kind=kind)
    members = (member,)
    version = ArtifactVersion(
        uuid4(),
        kind,
        "Фиксированный набор",
        "shared-vlm",
        record.section_id,
        record.section_title,
        members,
        manifest_hash(
            kind=kind, model="shared-vlm", section_id=record.section_id, members=members
        ),
        AT,
        "engineer:server",
        status="prepared" if kind == "fine_tune" else "queued",
    )
    return record, version


@pytest.mark.parametrize("bad", [False, True])
async def test_fixed_dataset_preserves_geometry_labels_and_verified_crop(bad):
    """Bad использует исходный текст; изменённая область не подменяет предложенную VLM."""
    record, version = prepared(bad=bad)
    images = Images()
    content = await ExportArtifactDataset(Registry(version), images).execute(version.id)
    assert content == await ExportArtifactDataset(Registry(version), images).execute(
        version.id
    )
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        jsonl = archive.read("examples.jsonl")
        row = json.loads(jsonl)
        assert manifest["artifact_manifest_sha256"] == version.manifest_sha256
        assert manifest["examples_sha256"] == hashlib.sha256(jsonl).hexdigest()
        assert manifest["member_count"] == 1 and manifest["missing_geometry_count"] == 0
        assert manifest["training_started"] is False
        assert row["original_basis"] == record.source.original_basis
        assert row["geometry"]["proposed_regions"][0]["bbox"]["x_min"] == 10
        assert row["geometry"]["display_regions"][0]["x_min"] == 50
        assert row["learning_use"] == ("negative" if bad else "positive")
        assert row["texts"][0]["target"] == ("original" if bad else "revised")
        assert archive.read(row["crop_files"][0]["path"]) == PNG
        assert images.calls == [record.crops[0], record.crops[0]]


async def test_vector_projection_compatibility_and_legacy_geometry_are_explicit():
    """Существующая индексация и fingerprint не меняются; геометрия не выдумывается."""
    record, version = prepared(kind="vector")
    assert version.members[0] == index_projection(CatalogEntry(record, True))
    content = await ExportArtifactDataset(Registry(version), Images()).execute(
        version.id
    )
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        row = json.loads(archive.read("examples.jsonl"))
        assert row["geometry_status"] == "not_recorded_in_version"
        assert "geometry" not in row
        assert json.loads(archive.read("manifest.json"))["missing_geometry_count"] == 1


@pytest.mark.parametrize(
    "fault", ["deleted", "manifest", "image", "limit", "concurrent_delete"]
)
async def test_invalid_or_concurrently_deleted_dataset_never_returns_archive(fault):
    """Не выдаёт неполный ZIP или изменённое содержимое под прежним SHA версии."""
    _, version = prepared()
    registry, images = Registry(version), Images()
    if fault == "deleted":
        registry.version = replace(version, deleted=True)
    elif fault == "manifest":
        registry.version = replace(
            version, members=({**version.members[0], "text": "Подмена"},)
        )
    elif fault == "image":
        images.content = PNG[:-1] + b"X"
    elif fault == "concurrent_delete":
        registry.change = replace(version, deleted=True, revision=1)
    exporter = ExportArtifactDataset(
        registry, images, max_bytes=10 if fault == "limit" else 100_000_000
    )
    error = (
        LookupError
        if fault == "deleted"
        else ReviewConflictError
        if fault == "concurrent_delete"
        else ReviewError
    )
    with pytest.raises(error):
        await exporter.execute(version.id)
