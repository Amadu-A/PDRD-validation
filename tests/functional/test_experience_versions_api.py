# tests/functional/test_experience_versions_api.py

"""Gateway → реестр → очередь Knowledge → crop: ручной выбор и границы служебных ключей."""

from dataclasses import replace
from uuid import UUID

import httpx
import pytest
from pdrd_experience_service.application.use_cases.artifacts import ManageArtifacts
from pdrd_experience_service.main import create_app as experience_app
from pdrd_knowledge_service.application.ports.experience_feed import ExperienceFeedError
from pdrd_knowledge_service.application.use_cases.index_experience import (
    SyncExperienceIndex,
)
from pdrd_knowledge_service.application.use_cases.index_experience_version import (
    RunExperienceVersion,
    SelectedExperienceFeed,
)
from pdrd_knowledge_service.application.use_cases.trusted_experience import (
    SearchTrustedExperience,
)
from pdrd_knowledge_service.infrastructure.experience_feed import HttpExperienceFeed
from pdrd_knowledge_service.infrastructure.experience_versions import (
    HttpExperienceVersionQueue,
)

from tests.experience_index_support import MemoryVectors, RecordingEmbedding
from tests.functional.artifact_support import MemoryArtifacts
from tests.functional.test_experience_catalog_api import catalog_flow as catalog_flow
from tests.functional.test_experience_catalog_api import listed
from tests.functional.test_experience_index_api import (
    INDEX_KEY,
)
from tests.functional.test_experience_index_api import (
    index_flow as index_flow,
)
from tests.functional.test_review_api import INTERNAL_KEY, client
from tests.functional.test_review_api import flow as flow
from tests.functional.test_reviewed_pdf_api import pdf_flow as pdf_flow
from tests.functional.test_reviewed_pdf_api import prepare


@pytest.fixture
def version_flow(index_flow):
    """Добавляет настоящий HTTP-реестр к контуру утверждения PDF."""
    container = index_flow.network.apps["experience"].state.container
    index_flow.artifacts = MemoryArtifacts()
    container = replace(
        container, artifacts=ManageArtifacts(index_flow.artifacts, index_flow.catalog)
    )
    index_flow.network.apps["experience"] = experience_app(container)
    return index_flow


async def test_manual_selected_version_builds_one_example_and_never_auto_indexes(
    version_flow,
):
    """Добавление в каталог не создаёт задачу; выбранная версия не включает остальные записи."""
    f = version_flow
    async with client(f) as browser:
        await prepare(browser, f)
        assert (await browser.get("/api/v1/experience-versions")).json() == {
            "items": [],
            "applied": [],
        }
        gold = (await listed(browser, tag="gold"))["items"][0]
        curated = await browser.patch(
            f"/api/v1/experience/{gold['id']}",
            json={
                "expected_revision": 0,
                "fields": {"section_id": "СП 1:6", "section_title": "СП 1, раздел 6"},
            },
        )
        assert curated.status_code == 200, curated.text
        response = await browser.post(
            "/api/v1/experience-versions",
            json={
                "kind": "vector",
                "name": "Ручная версия",
                "model": "shared-embedding",
                "items": [{"id": gold["id"], "revision": 1}],
            },
        )
        assert response.status_code == 200, response.text
        version = response.json()
        assert version["status"] == "queued"
    source = HttpExperienceFeed("http://experience", INDEX_KEY, transport=f.network)
    embedding, vectors = RecordingEmbedding(), MemoryVectors()
    index = SyncExperienceIndex(source, embedding, vectors, "unused", "a" * 16, 2)
    worker = RunExperienceVersion(
        HttpExperienceVersionQueue(source), index, "worker", "shared-embedding"
    )
    result = await worker.execute()
    assert result["examples"] == 1 and result["indexed"] == 1
    assert {item.payload["example_id"] for item in vectors.records.values()} == {
        gold["id"]
    }
    assert all(
        item.payload["section_id"] == "СП 1:6" for item in vectors.records.values()
    )
    assert (await f.artifacts.get(UUID(version["id"]))).status == "ready"
    assert (await worker.execute()) == {"claimed": False}
    job = await HttpExperienceVersionQueue(source).read(
        version_id=UUID(version["id"]),
        model="shared-embedding",
        identity="a" * 16,
        dimension=2,
    )
    assert len(job.members) == 1 and job.manifest_sha256 == version["manifest_sha256"]
    with pytest.raises(ExperienceFeedError):
        await HttpExperienceVersionQueue(source).read(
            version_id=job.id,
            model="another-model",
            identity="a" * 16,
            dimension=2,
        )
    search = SearchTrustedExperience(
        embedding,
        vectors,
        SelectedExperienceFeed(source, job.members),
        job.collection,
        "shared-embedding",
        job.identity,
        enabled=True,
        require_section=True,
    )
    calls = len(embedding.calls)
    assert not (await search.execute(["Замечание"]))[0].sources
    assert len(embedding.calls) == calls
    assert (
        len((await search.execute(["Замечание"], section_id=job.section_id))[0].sources)
        == 1
    )
    assert not (await search.execute(["Замечание"], section_id="Другой СП"))[0].sources
    async with client(f) as browser:
        ready = (
            await browser.get(f"/api/v1/experience-versions/{version['id']}")
        ).json()
        assert not ready["quality_approved"]
        assert (
            await browser.post(
                f"/api/v1/experience-versions/{version['id']}/apply",
                json={"expected_revision": ready["revision"]},
            )
        ).status_code == 422


async def test_browser_cannot_forge_job_status_manifest_actor_or_use_index_key_for_catalog(
    version_flow,
):
    """Индексный ключ пишет только аренду/статус; служебные пути отсутствуют у Gateway."""
    f = version_flow
    async with client(f) as browser:
        forged = await browser.post(
            "/api/v1/experience-versions",
            json={
                "kind": "vector",
                "name": "Версия",
                "model": "model",
                "items": [{"id": str(UUID(int=1)), "revision": 0}],
                "quality_approved": True,
            },
        )
        assert forged.status_code == 422
        assert (
            await browser.post("/internal/v1/experience-index/versions/claim", json={})
        ).status_code == 404
    async with httpx.AsyncClient(
        transport=f.network, base_url="http://experience"
    ) as internal:
        claim = {
            "worker": "test",
            "model": "model",
            "identity": "a" * 16,
            "dimension": 2,
        }
        assert (
            await internal.post(
                "/internal/v1/experience-index/versions/claim",
                json=claim,
                headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
            )
        ).status_code == 403
        assert (
            await internal.get(
                "/internal/v1/experience-versions",
                headers={
                    "Authorization": f"Bearer {INDEX_KEY}",
                    "X-Review-Actor": "engineer:test",
                },
            )
        ).status_code == 403
