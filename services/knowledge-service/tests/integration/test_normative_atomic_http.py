# services/knowledge-service/tests/integration/test_normative_atomic_http.py

"""Настоящие HTTP-контракты атомарного каталога и шлюза с подменой только persistence/PDF."""

from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pdrd_api_gateway.core.settings import KnowledgeServiceSettings
from pdrd_api_gateway.infrastructure.knowledge.normative_catalog_management import (
    HttpNormativeCatalogManager,
)
from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    BuildNormativeRequirements,
    NormativeRequirementCatalog,
)
from pdrd_knowledge_service.domain.normative_catalog import CatalogArea
from pdrd_knowledge_service.transport.http.dependencies import get_container
from pdrd_knowledge_service.transport.http.routers.normative_requirements import router

from ..atomic_normative_support import (
    ACTOR_ID,
    DOCUMENT_ID,
    Extractor,
    State,
    Storage,
    UnitOfWork,
    proof_payload,
)


def app_and_catalog():
    """Собирает реальные маршруты, application и HTTP adapter без БД/GPU."""
    state = State()

    def factory():
        """Создаёт независимую транзакцию общей имитации БД."""
        return UnitOfWork(state)

    catalog = NormativeRequirementCatalog(factory)
    app = FastAPI()
    app.dependency_overrides[get_container] = lambda: SimpleNamespace(
        normative_requirements=catalog
    )
    app.include_router(router)
    return app, state, catalog


async def test_queue_worker_paginated_http_and_gateway_adapter_keep_exact_sources_and_status():
    """Публичный adapter читает весь каталог с точным source SHA, пунктом, страницами и свидетельством."""
    app, state, _ = app_and_catalog()
    manager = HttpNormativeCatalogManager(
        settings=KnowledgeServiceSettings(base_url="http://knowledge.test"),
        transport=httpx.ASGITransport(app=app),
    )
    status = await manager.requirement_index_state(document_id=DOCUMENT_ID)
    assert status["state"] == "not_indexed" and state.outbox == []
    proof = await manager.record_status_evidence(
        document_id=DOCUMENT_ID, payload=proof_payload(), actor_id=ACTOR_ID
    )
    assert proof["verified_by"] == str(ACTOR_ID)
    assert (await manager.status_evidence(document_id=DOCUMENT_ID))[
        "status_evidence"
    ] == [proof]
    queued = await manager.queue_requirements(document_id=DOCUMENT_ID)
    assert (await manager.requirement_index_state(document_id=DOCUMENT_ID))[
        "state"
    ] == "queued"
    await BuildNormativeRequirements(
        lambda: UnitOfWork(state), Storage(), Extractor()
    ).execute(document_id=DOCUMENT_ID, generation_id=queued["generation_id"])
    page = await manager.list_requirements(
        document_id=DOCUMENT_ID, query={"limit": 1, "assessed_on": "2026-10-20"}
    )
    all_rows = list(page["requirements"])
    while page["next_offset"] is not None:
        page = await manager.list_requirements(
            document_id=DOCUMENT_ID,
            query={
                "generation_id": page["generation_id"],
                "limit": 1,
                "offset": page["next_offset"],
                "assessed_on": "2026-10-20",
            },
        )
        all_rows.extend(page["requirements"])
    assert len(all_rows) == page["count"]
    assert all(
        row["source_sha256"] == state.documents[str(DOCUMENT_ID)].sha256
        for row in all_rows
    )
    target = next(row for row in all_rows if row["clause"] == "5.2")
    assert [part["page"] for part in target["source_fragments"]] == [1, 2]
    assert target["exceptions"] and target["applicability_conditions"]
    assert page["status_evidence"][0] == proof
    assert page["vlm_calls"] == page["embedding_calls"] == 0


async def test_internal_status_contract_rejects_author_spoofing_and_invalid_proof():
    """Служебные поля не принимаются в JSON даже от доверенного внутреннего клиента."""
    app, state, _ = app_and_catalog()
    path = f"/internal/v1/normative/documents/{DOCUMENT_ID}/status-evidence"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.post(path, json=proof_payload())).status_code == 422
        headers = {"X-PDRD-Actor-ID": str(ACTOR_ID)}
        for body in (
            proof_payload(verified_by=str(ACTOR_ID)),
            proof_payload(source_url="file:///secret"),
            proof_payload(requirement_binding="because_model_says_so"),
            proof_payload(clause_scope=["5." + "1" * 80]),
        ):
            assert (
                await client.post(path, headers=headers, json=body)
            ).status_code == 422
        assert state.proofs == {}
        state.documents[str(DOCUMENT_ID)] = replace(
            state.documents[str(DOCUMENT_ID)],
            area=CatalogArea.USER_PACKAGE,
            owner_user_id=ACTOR_ID,
        )
        assert (
            await client.post(path, headers=headers, json=proof_payload())
        ).status_code == 404
        assert (
            await client.post(path.replace("status-evidence", "requirements/index"))
        ).status_code == 404
        assert state.proofs == {} and state.outbox == []


async def test_unexpected_programming_error_is_not_disguised_as_missing_document(
    monkeypatch,
):
    """KeyError из кода остаётся исключением, а не успешным пустым ответом либо HTTP 404."""
    app, _, _ = app_and_catalog()

    async def broken(self, **kwargs):
        """Воспроизводит непредвиденный дефект программной обработки свойства."""
        raise KeyError("unhandled_property")

    monkeypatch.setattr(NormativeRequirementCatalog, "index_state", broken)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        with pytest.raises(KeyError, match="unhandled_property"):
            await client.get(
                f"/internal/v1/normative/documents/{DOCUMENT_ID}/requirements/index"
            )
