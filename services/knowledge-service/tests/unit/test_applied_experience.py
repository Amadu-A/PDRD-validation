# services/knowledge-service/tests/unit/test_applied_experience.py

"""Рабочий E читает только назначенную версию раздела и отзывает изменённый допуск."""

import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from pdrd_knowledge_service.application.ports.experience_feed import ExperienceFeedError
from pdrd_knowledge_service.application.use_cases.applied_experience import (
    SearchAppliedExperience,
)
from pdrd_knowledge_service.application.use_cases.health import CheckReadiness
from pdrd_knowledge_service.infrastructure.experience_feed import (
    HttpExperienceFeed,
    parse_example,
)
from pdrd_knowledge_service.infrastructure.experience_versions import (
    HttpExperienceVersionQueue,
)
from pdrd_knowledge_service.transport.http.dependencies import get_container
from pdrd_knowledge_service.transport.http.routers.search import router

from tests.experience_index_support import signed, trusted_example

from .test_experience_quality import report
from .test_trusted_experience import services


def version(*, number=1, section="СП 1:6"):
    """Синтетический отчёт проверяет контракт, но не доказывает качество реальной VLM."""
    item = parse_example(
        signed({**trusted_example(number=number).data, "section_id": section})
    )
    body = {
        "schema_version": 1,
        "kind": "vector",
        "model": "model",
        "section_id": section,
        "members": [item.data],
    }
    manifest = hashlib.sha256(
        json.dumps(
            body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
    identifier = UUID(int=100 + number)
    collection = f"pdrd_e_identity_{identifier.hex}"
    quality = {
        **report(),
        "model": "model",
        "version_id": str(identifier),
        "manifest_sha256": manifest,
        "section_id": section,
        "dimension": 2,
        "collection": collection,
        "evaluation_document_sha256": [f"{index:064x}" for index in range(1, 6)],
        "evaluated_at": "2026-09-29T10:00:00+00:00",
    }
    return item, {
        **body,
        "id": str(identifier),
        "manifest_sha256": manifest,
        "status": "ready",
        "embedding_identity": "identity",
        "dimension": 2,
        "collection": collection,
        "deleted": False,
        "quality_approved": True,
        "quality_report": quality,
    }


def owner(versions):
    """Возвращает изменяемое серверное назначение через настоящий HTTP-адаптер."""
    calls = []

    def handle(request):
        """Проверяет URL раздела и отдельный индексный ключ без Review полномочий."""
        assert request.url.path == "/internal/v1/experience-index/applied"
        assert request.headers["authorization"] == "Bearer private-index-key"
        section = request.url.params["section_id"]
        calls.append(section)
        return httpx.Response(200, json={"item": versions.get(section)})

    source = HttpExperienceFeed(
        "http://experience", "private-index-key", transport=httpx.MockTransport(handle)
    )
    return HttpExperienceVersionQueue(source), calls


async def prepared(*items):
    """Создаёт точки отдельно от включения рабочего E и регистрации назначения."""
    examples = tuple(item for item, _ in items)
    feed, embedding, vectors, index, search = services(examples)
    await index.execute()
    versions = {data["section_id"]: data for _, data in items}
    queue, calls = owner(versions)
    vectors.searches.clear()
    embedding.calls.clear()
    return (
        SearchAppliedExperience(search, queue, 2, enabled=True),
        versions,
        feed,
        embedding,
        vectors,
        calls,
    )


@pytest.mark.parametrize("condition", ["disabled", "no_section", "no_applied"])
async def test_no_working_assignment_never_calls_gpu_or_legacy_collection(condition):
    """Флаг, раздел и применение обязательны; отсутствие назначения означает пустой E."""
    search, versions, _, embedding, vectors, calls = await prepared(version())
    if condition == "disabled":
        search = replace(search, enabled=False)
    if condition == "no_applied":
        versions.clear()
    results = await search.execute(
        ["Шлейф", "Шлейф"], section_id=None if condition == "no_section" else "СП 1:6"
    )
    assert [result.query for result in results] == ["Шлейф", "Шлейф"]
    assert all(not result.sources for result in results)
    assert not embedding.calls and not vectors.searches
    assert len(calls) == (1 if condition == "no_applied" else 0)


async def test_each_normative_section_uses_only_its_applied_collection_and_members():
    """Отдельные коллекции и scope исключают чужой раздел даже при неверном ответе Qdrant."""
    first, second = version(), version(number=2, section="СП 2:7")
    search, _, _, _, vectors, calls = await prepared(first, second)
    for item, data in (first, second):
        result = (await search.execute(["Шлейф"], section_id=data["section_id"]))[0]
        assert [source.example_id for source in result.sources] == [
            str(item.reference.example_id)
        ]
    assert [call["collection"] for call in vectors.searches] == [
        first[1]["collection"],
        second[1]["collection"],
    ]
    assert calls == ["СП 1:6", "СП 1:6", "СП 2:7", "СП 2:7"]


@pytest.mark.parametrize("change", ["revoked", "deleted", "replaced", "reapproved"])
async def test_assignment_changed_during_gpu_wait_does_not_return_previous_context(
    change,
):
    """Допуск повторно проверяется после retrieval перед выдачей контекста в анализ."""
    first = version()
    search, versions, _, embedding, _, _ = await prepared(first)

    def update():
        """Отзыв и удаление владельцем возвращают null; замена меняет фиксированный состав."""
        if change in {"revoked", "deleted"}:
            versions.clear()
        elif change == "replaced":
            versions["СП 1:6"] = version(number=2)[1]
        else:
            versions["СП 1:6"]["quality_report"]["dataset_sha256"] = "b" * 64

    embedding.after_embed = update
    assert not (await search.execute(["Шлейф"], section_id="СП 1:6"))[0].sources


async def test_catalog_edit_cannot_use_old_vector_payload_as_current_text():
    """Даже ошибочно оставшееся назначение не переносит доверие на изменённый пример."""
    search, _, feed, _, _, _ = await prepared(version())
    feed.current.clear()
    assert not (await search.execute(["Шлейф"], section_id="СП 1:6"))[0].sources


@pytest.mark.parametrize(
    "problem",
    [
        "model",
        "identity",
        "dimension",
        "collection",
        "manifest",
        "section",
        "deleted",
        "status",
        "not_approved",
        "quality_model",
        "quality_dimension",
        "quality_version",
        "quality_manifest",
        "quality_identity",
        "quality_collection",
        "quality_section",
        "top_k",
        "min_score",
        "no_improvement",
        "leaked_document",
        "duplicate_document",
        "short_evaluation",
        "invalid_timestamp",
        "non_utc",
        "missing_report",
    ],
)
async def test_incompatible_assignment_fails_before_embedding_or_qdrant(problem):
    """Нельзя применить отчёт другой версии, модели, параметров либо зависимый набор."""
    search, versions, _, embedding, vectors, _ = await prepared(version())
    data = versions["СП 1:6"]
    quality = data["quality_report"]
    if problem in {"model", "identity", "collection", "manifest", "section"}:
        field = {
            "identity": "embedding_identity",
            "manifest": "manifest_sha256",
            "section": "section_id",
        }.get(problem, problem)
        data[field] = "other"
    elif problem == "dimension":
        data["dimension"] = 3
    elif problem == "deleted":
        data["deleted"] = True
    elif problem == "status":
        data["status"] = "failed"
    elif problem == "not_approved":
        data["quality_approved"] = False
    elif problem.startswith("quality_"):
        field = {
            "quality_version": "version_id",
            "quality_manifest": "manifest_sha256",
            "quality_identity": "embedding_identity",
            "quality_section": "section_id",
        }.get(problem, problem.removeprefix("quality_"))
        quality[field] = 3 if field == "dimension" else "other"
    elif problem == "top_k":
        quality["top_k"] = 4
    elif problem == "min_score":
        quality["min_score"] = 0.5
    elif problem == "no_improvement":
        quality["with_experience"] = copy.deepcopy(quality["baseline"])
    elif problem == "leaked_document":
        quality["evaluation_document_sha256"][0] = "a" * 64
    elif problem == "duplicate_document":
        quality["evaluation_document_sha256"][1] = quality[
            "evaluation_document_sha256"
        ][0]
    elif problem == "short_evaluation":
        quality["evaluation_document_sha256"].pop()
    elif problem == "invalid_timestamp":
        quality["evaluated_at"] = "2026-09-29T10:00:00"
    elif problem == "non_utc":
        quality["evaluated_at"] = "2026-09-29T10:00:00+03:00"
    else:
        del data["quality_report"]
    with pytest.raises(ExperienceFeedError):
        await search.execute(["Шлейф"], section_id="СП 1:6")
    assert not embedding.calls and not vectors.searches


async def test_owner_outage_does_not_fallback_to_approved_cached_collection():
    """Недоступность канала контроля отличается от отсутствия рабочей версии."""
    search, _, _, embedding, vectors, _ = await prepared(version())
    failing = HttpExperienceVersionQueue(
        HttpExperienceFeed(
            "http://experience",
            "key",
            transport=httpx.MockTransport(lambda request: httpx.Response(503)),
        )
    )
    with pytest.raises(ExperienceFeedError):
        await replace(search, versions=failing).execute(["Шлейф"], section_id="СП 1:6")
    assert not embedding.calls and not vectors.searches


async def test_http_search_passes_normative_section_to_applied_version_resolver():
    """Публичный внутренний контракт действительно доводит раздел до рабочего resolver."""
    selected = version()
    search, _, _, _, vectors, calls = await prepared(selected)
    app = FastAPI()
    app.dependency_overrides[get_container] = lambda: SimpleNamespace(
        search_experience=search
    )
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://knowledge"
    ) as client:
        response = await client.post(
            "/internal/v1/search/experience",
            json={"queries": ["Шлейф"], "section_id": "СП 1:6"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["results"][0]["sources"][0]["example_id"] == str(
            selected[0].reference.example_id
        )
        assert calls == ["СП 1:6", "СП 1:6"]
        assert vectors.searches[0]["collection"] == selected[1]["collection"]
        response = await client.post(
            "/internal/v1/search/experience", json={"queries": ["Шлейф"]}
        )
        assert (
            response.status_code == 200
            and response.json()["results"][0]["sources"] == []
        )
        assert len(calls) == 2


async def test_readiness_of_section_versions_does_not_probe_legacy_collection():
    """Readiness сохраняет PostgreSQL/shared-embedding/Qdrant проверки без фиктивной коллекции E."""
    probe = SimpleNamespace(is_ready=AsyncMock(return_value=True))
    vectors = SimpleNamespace(
        is_ready=AsyncMock(return_value=True),
        collection_exists=AsyncMock(return_value=True),
    )
    result = await CheckReadiness(probe, probe, vectors, "normative", None).execute()
    assert (
        result.experience_collection and result.qdrant and result.normative_collection
    )
    vectors.collection_exists.assert_awaited_once_with("normative")
