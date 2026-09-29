# tests/functional/test_artifact_dataset_api.py

"""Gateway → Experience → immutable PNG: архив редакций, а не повторный экспорт каталога."""

import io
import json
import zipfile
from dataclasses import replace

import httpx
import pytest
from pdrd_experience_service.application.use_cases.export_artifact_dataset import (
    ExportArtifactDataset,
)
from pdrd_experience_service.main import create_app as experience_app

from tests.functional.test_experience_catalog_api import catalog_flow as catalog_flow
from tests.functional.test_experience_catalog_api import listed
from tests.functional.test_experience_index_api import INDEX_KEY
from tests.functional.test_experience_index_api import index_flow as index_flow
from tests.functional.test_experience_versions_api import version_flow as version_flow
from tests.functional.test_review_api import client
from tests.functional.test_review_api import flow as flow
from tests.functional.test_reviewed_pdf_api import pdf_flow as pdf_flow
from tests.functional.test_reviewed_pdf_api import prepare


@pytest.fixture
def dataset_flow(version_flow):
    """В реальный HTTP-контур внедряется только сценарий экспорта снимка."""
    container = version_flow.network.apps["experience"].state.container
    version_flow.network.apps["experience"] = experience_app(
        replace(
            container,
            export_artifact_dataset=ExportArtifactDataset(
                version_flow.artifacts, version_flow.crops
            ),
        )
    )
    return version_flow


async def test_prepared_dataset_export_keeps_snapshot_after_catalog_curation_and_removal(
    dataset_flow,
):
    """Редакция и выбранные PNG остаются прежними после правок и удаления из каталога."""
    f = dataset_flow
    async with client(f) as browser:
        await prepare(browser, f)
        gold = (await listed(browser, tag="gold"))["items"][0]
        scoped = await browser.patch(
            f"/api/v1/experience/{gold['id']}",
            json={
                "expected_revision": 0,
                "fields": {"section_id": "СП 1:6", "section_title": "Раздел 6"},
            },
        )
        assert scoped.status_code == 200, scoped.text
        frozen_text = scoped.json()["text"]
        created = await browser.post(
            "/api/v1/experience-versions",
            json={
                "kind": "fine_tune",
                "name": "Набор для эксперимента",
                "model": "shared-vlm",
                "items": [{"id": gold["id"], "revision": 1}],
            },
        )
        assert created.status_code == 200, created.text
        version = created.json()
        assert version["status"] == "prepared"
        response = await browser.get(
            f"/api/v1/experience-versions/{version['id']}/dataset"
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["cache-control"] == "no-store"
        updated = await browser.patch(
            f"/api/v1/experience/{gold['id']}",
            json={
                "expected_revision": 1,
                "fields": {"text": "Новая редакция каталога"},
            },
        )
        assert updated.status_code == 200, updated.text
        removed = await browser.post(
            "/api/v1/experience/delete-selection",
            json={"items": [{"id": gold["id"], "revision": 2}]},
        )
        assert removed.status_code == 200, removed.text
        exported = await browser.get(
            f"/api/v1/experience-versions/{version['id']}/dataset"
        )
        assert exported.status_code == 200, exported.text
        assert exported.content == response.content
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            row = json.loads(archive.read("examples.jsonl"))
            assert manifest["artifact_manifest_sha256"] == version["manifest_sha256"]
            assert row["example_revision"] == 1 and row["text"] == frozen_text
            assert row["geometry_status"] == "recorded"
            assert len(row["geometry"]["issue_regions"]) == 1
            assert archive.read(row["crop_files"][0]["path"]).startswith(
                b"\x89PNG\r\n\x1a\n"
            )
        registry = (await browser.get("/api/v1/experience-versions")).json()
        assert len(registry["items"]) == 1
        assert (
            registry["applied"] == []
        )  # Экспорт не индексирует и не применяет версию.
        deleted = await browser.post(
            f"/api/v1/experience-versions/{version['id']}/delete",
            json={"expected_revision": 0},
        )
        assert deleted.status_code == 200, deleted.text
        assert (
            await browser.get(f"/api/v1/experience-versions/{version['id']}/dataset")
        ).status_code == 404


async def test_index_key_cannot_export_training_dataset(dataset_flow):
    """Индексный worker не получает ZIP через доверенный канал инженерного каталога."""
    async with httpx.AsyncClient(
        transport=dataset_flow.network, base_url="http://experience"
    ) as internal:
        response = await internal.get(
            "/internal/v1/experience-versions/00000000-0000-0000-0000-000000000001/dataset",
            headers={
                "Authorization": f"Bearer {INDEX_KEY}",
                "X-Review-Actor": "engineer:test",
            },
        )
        assert response.status_code == 403
