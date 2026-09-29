# tests/functional/test_experience_index_api.py

"""Review → реальный crop/закрытый feed → Knowledge: индекс, выдача и немедленный отзыв E."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

import fitz
import httpx
import pytest
from fastapi import FastAPI
from pdrd_analysis_service.transport.http.schemas import ExperienceSourcePayload
from pdrd_experience_service.application.use_cases.index_feed import ReadIndexFeed
from pdrd_experience_service.main import create_app as experience_app
from pdrd_knowledge_service.application.use_cases.index_experience import (
    SyncExperienceIndex,
)
from pdrd_knowledge_service.application.use_cases.trusted_experience import (
    SearchTrustedExperience,
)
from pdrd_knowledge_service.infrastructure.experience_feed import HttpExperienceFeed
from pdrd_knowledge_service.transport.http.dependencies import get_container
from pdrd_knowledge_service.transport.http.routers.search import router as search_router
from pydantic import SecretStr

from tests.experience_index_support import MemoryVectors, RecordingEmbedding
from tests.functional.test_experience_catalog_api import catalog_flow as catalog_flow
from tests.functional.test_experience_catalog_api import listed
from tests.functional.test_review_api import INTERNAL_KEY, client, command
from tests.functional.test_review_api import flow as flow
from tests.functional.test_reviewed_pdf_api import pdf_flow as pdf_flow
from tests.functional.test_reviewed_pdf_api import prepare

INDEX_KEY = "index-reader-" + "i" * 52
PREFIX = "/internal/v1/experience-index"


@pytest.fixture
def index_flow(catalog_flow):
    """Добавляет read-only канал к действующему трёхсервисному HTTP-контуру."""
    container = catalog_flow.network.apps["experience"].state.container
    container = replace(
        container,
        settings=container.settings.model_copy(
            update={"index_key": SecretStr(INDEX_KEY)}
        ),
        index_feed=ReadIndexFeed(catalog_flow.catalog, catalog_flow.crops),
    )
    catalog_flow.network.apps["experience"] = experience_app(container)
    return catalog_flow


def index_services(flow):
    """HTTP-владелец настоящий; внешние GPU и Qdrant заменены только тестовыми портами."""
    feed = HttpExperienceFeed("http://experience", INDEX_KEY, transport=flow.network)
    embedding, vectors = RecordingEmbedding(), MemoryVectors()
    index = SyncExperienceIndex(
        feed, embedding, vectors, "trusted", "identity", 2, page_size=1
    )
    search = SearchTrustedExperience(
        embedding, vectors, feed, "trusted", "model", "identity", enabled=True
    )
    app = FastAPI()
    app.state.container = SimpleNamespace(search_experience=search)
    app.dependency_overrides[get_container] = lambda: app.state.container
    app.include_router(search_router)
    return feed, embedding, vectors, index, app


@pytest.mark.parametrize("reject_first", [False, True])
async def test_approved_review_indexes_only_eligible_regions_and_transports_polarity(
    index_flow, reject_first
):
    """Text-only находка не индексируется; Bad с ранее принятой областью и Gold сохраняют назначение."""
    flow = index_flow
    async with client(flow) as browser:
        await prepare(browser, flow, reject_first=reject_first)
    feed, embedding, vectors, index, app = index_services(flow)
    examples, _ = await feed.page(after=None, limit=100)
    assert len(examples) == 2 and {item.data["tag"] for item in examples} == (
        {"bad", "gold"} if reject_first else {"wise", "gold"}
    )
    for example in examples:
        content = await feed.crop(example=example, index=0)
        with fitz.open(stream=content, filetype="png") as image:
            assert image.page_count == 1
    result = await index.execute()
    assert result["indexed"] == 2 and len(vectors.records) == 2
    assert all(
        value.image_bytes.startswith(b"\x89PNG")
        for call, _ in embedding.calls
        for value in call
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://knowledge"
    ) as knowledge:
        response = await knowledge.post(
            "/internal/v1/search/experience", json={"queries": ["Обозначение"]}
        )
        assert response.status_code == 200, response.text
        sources = response.json()["results"][0]["sources"]
        assert len(sources) == 2
        for data in sources:
            source = ExperienceSourcePayload.model_validate(data).to_domain()
            assert source.example_id and source.example_revision == 0
            assert not source.verified_fixed and source.after_page is None
            assert source.decision == (
                "rejected" if source.tag == "bad" else "accepted"
            )


async def test_catalog_deactivation_immediately_excludes_result_before_vector_cleanup(
    index_flow,
):
    """Вектор ещё есть, но владелец уже не подтверждает редакцию; следующий цикл удаляет точку."""
    flow = index_flow
    feed, _, vectors, index, app = index_services(flow)
    async with client(flow) as browser:
        await prepare(browser, flow)
        await index.execute()
        gold = (await listed(browser, tag="gold"))["items"][0]
        response = await browser.patch(
            f"/api/v1/experience/{gold['id']}",
            json={"expected_revision": 0, "fields": {"active": False}},
        )
        assert response.status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://knowledge"
    ) as knowledge:
        response = await knowledge.post(
            "/internal/v1/search/experience", json={"queries": ["Обозначение"]}
        )
        assert [
            source["tag"] for source in response.json()["results"][0]["sources"]
        ] == ["wise"]
    assert len(vectors.records) == 2
    result = await index.execute()
    assert result["deleted"] == 1 and len(vectors.records) == 1
    assert len((await feed.page(after=None, limit=100))[0]) == 1


async def test_revoked_area_blocks_feed_crop_and_search_without_erasing_audit(
    index_flow,
):
    """Отзыв ранее принятой области сразу закрывает автоматическое использование."""
    flow = index_flow
    feed, _, _, index, app = index_services(flow)
    async with client(flow) as browser:
        revision = await prepare(browser, flow)
        await index.execute()
        wise = (await listed(browser, tag="wise"))["items"][0]
        old = next(
            item
            for item in (await feed.page(after=None, limit=100))[0]
            if item.data["tag"] == "wise"
        )
        assert (
            await command(
                browser,
                flow,
                revision,
                action="revoke_area",
                finding_id="vlm:1",
                expected_confirmation_revision=1,
                reason="Отозвана область",
            )
        ).status_code == 200
        assert (await listed(browser))["total"] == 2
    assert await feed.verify((old.reference,)) == ()
    async with httpx.AsyncClient(
        transport=flow.network,
        base_url="http://experience",
        headers={"Authorization": f"Bearer {INDEX_KEY}"},
    ) as reader:
        response = await reader.get(
            f"{PREFIX}/crops/{wise['id']}/0",
            params={"fingerprint": old.reference.fingerprint},
        )
        assert response.status_code == 409
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://knowledge"
    ) as knowledge:
        result = (
            await knowledge.post(
                "/internal/v1/search/experience", json={"queries": ["Обозначение"]}
            )
        ).json()
        assert all(
            source["tag"] != "wise" for source in result["results"][0]["sources"]
        )


async def test_edited_rejected_enters_index_only_after_adjudication_and_restarts_after_curation(
    index_flow,
):
    """Обе формулировки индексируются только по явному both; новая правка сбрасывает прежнюю оценку."""
    flow = index_flow
    feed, _, vectors, index, _ = index_services(flow)
    async with client(flow) as browser:
        await prepare(browser, flow, reject_first=True)
        await index.execute()
        bad = (await listed(browser, tag="bad"))["items"][0]
        url = f"/api/v1/experience/{bad['id']}"
        assert (
            await browser.patch(
                url,
                json={"expected_revision": 0, "fields": {"text": "Исправленный отказ"}},
            )
        ).status_code == 200
        assert len((await feed.page(after=None, limit=100))[0]) == 1
        assert (await index.execute())["deleted"] == 1
        response = await browser.patch(
            url,
            json={
                "expected_revision": 1,
                "fields": {
                    "negative_target": "both",
                    "rejection_reason": "Обе формулировки неверны",
                },
            },
        )
        assert response.status_code == 200
        assert (await index.execute())["indexed"] == 2 and len(vectors.records) == 3
        edited = next(
            item
            for item in (await feed.page(after=None, limit=100))[0]
            if item.data["tag"] == "edited"
        )
        assert {item["target"] for item in edited.data["texts"]} == {
            "original",
            "revised",
        }
        response = await browser.patch(
            url, json={"expected_revision": 2, "fields": {"text": "Новая формулировка"}}
        )
        assert response.status_code == 200
        assert await feed.verify((edited.reference,)) == ()
        assert (await index.execute())["deleted"] == 2


@pytest.mark.parametrize("key", ["", INTERNAL_KEY, "wrong-key"])
async def test_index_feed_requires_its_own_server_key(index_flow, key):
    """Браузер и канал записи Review не получают чтение E; индексатор не получает запись Review."""
    async with httpx.AsyncClient(
        transport=index_flow.network,
        base_url="http://experience",
        headers={"Authorization": f"Bearer {key}"},
    ) as client:
        assert (await client.get(PREFIX + "/feed")).status_code == 403
        assert (
            await client.post(PREFIX + "/verify", json={"references": []})
        ).status_code == 403
    async with httpx.AsyncClient(
        transport=index_flow.network,
        base_url="http://experience",
        headers={"Authorization": f"Bearer {INDEX_KEY}"},
    ) as reader:
        assert (await reader.get(PREFIX + "/feed")).status_code == 200
        response = await reader.get(f"/internal/v1/reviews/{index_flow.job_id}")
        assert response.status_code in {403, 404}


async def test_feed_validates_limits_and_never_accepts_client_text(index_flow):
    """Клиент передаёт только ссылку на редакцию, без назначения источника и координат."""
    async with httpx.AsyncClient(
        transport=index_flow.network,
        base_url="http://experience",
        headers={"Authorization": f"Bearer {INDEX_KEY}"},
    ) as reader:
        assert (
            await reader.get(PREFIX + "/feed", params={"limit": 101})
        ).status_code == 422
        reference = {
            "example_id": str(UUID(int=1)),
            "example_revision": True,
            "fingerprint": "a" * 64,
        }
        assert (
            await reader.post(PREFIX + "/verify", json={"references": [reference]})
        ).status_code == 422
        reference.update(example_revision=0, text="Подмена")
        assert (
            await reader.post(PREFIX + "/verify", json={"references": [reference]})
        ).status_code == 422


async def test_owner_outage_returns_503_instead_of_cached_context(index_flow):
    """Проверка текущей редакции обязательна даже при наличии старых vectors."""
    flow = index_flow
    async with client(flow) as browser:
        await prepare(browser, flow)
    _, _, _, index, app = index_services(flow)
    await index.execute()
    container = flow.network.apps["experience"].state.container
    flow.network.apps["experience"] = experience_app(
        replace(container, index_feed=None)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://knowledge"
    ) as knowledge:
        response = await knowledge.post(
            "/internal/v1/search/experience", json={"queries": ["Обозначение"]}
        )
        assert response.status_code == 503 and "sources" not in response.json()
