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
from pdrd_knowledge_service.application.use_cases.recover_normative_requirements import (
    RecoverNormativeRequirements,
)
from pdrd_knowledge_service.domain.normative_catalog import CatalogArea
from pdrd_knowledge_service.transport.http.dependencies import get_container
from pdrd_knowledge_service.transport.http.routers.normative_requirements import (
    readiness_router,
    router,
)

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
        normative_requirements=catalog,
        normative_requirement_recovery=RecoverNormativeRequirements(catalog),
    )
    app.include_router(router)
    app.include_router(readiness_router)
    return app, state, catalog


async def test_section_readiness_http_is_read_only_and_distinguishes_chunk_atomic_and_legal_status():
    """API и Gateway adapter не запускают построение при открытии каталога."""
    app, state, catalog = app_and_catalog()
    manager = HttpNormativeCatalogManager(
        settings=KnowledgeServiceSettings(base_url="http://knowledge.test"),
        transport=httpx.ASGITransport(app=app),
    )
    section_id = state.documents[str(DOCUMENT_ID)].section_id
    report = await manager.atomic_readiness(section_id=section_id)
    row = report["documents"][0]
    assert row["search_state"] == "ready" and row["atomic_state"] == "not_indexed"
    assert (
        row["legal_status"] == "unknown" and not state.outbox and not state.generations
    )
    queued = await catalog.queue(document_id=DOCUMENT_ID)
    await BuildNormativeRequirements(
        lambda: UnitOfWork(state), Storage(), Extractor()
    ).execute(document_id=DOCUMENT_ID, generation_id=queued["generation_id"])
    report = await manager.atomic_readiness(section_id=section_id)
    assert report["sections"][0]["atomic_complete"] == 1
    assert report["documents"][0]["status_evidence_count"] == 0


@pytest.mark.parametrize("value,expected", [("4", 1), ("5", 0), ("7", 0)])
async def test_persisted_catalog_to_http_nfirst_and_finalization_control_without_vlm(
    value, expected
):
    """Стендовая норма извлекается и хранится worker, затем проверяется через настоящий HTTP reader.

    Свидетельство учебное: этот тест проверяет lifecycle, а не действительность реального СП.
    Findings не подставляются ни в extractor, ни в reader, ни в Finalization.
    """
    from pdrd_analysis_service.application.independent_normative import (
        IndependentNormativeReview,
    )
    from pdrd_analysis_service.application.use_cases.document_context import (
        DocumentPage,
    )
    from pdrd_analysis_service.application.use_cases.finalization import (
        FinalizeFindings,
    )
    from pdrd_analysis_service.domain.document_context import document_fact_from_mapping
    from pdrd_analysis_service.infrastructure.normative_requirements import (
        HttpNormativeRequirementsReader,
    )
    from pdrd_knowledge_service.domain.normative_indexing import NormativeTextPage

    app, state, catalog = app_and_catalog()
    section_id = str(state.documents[str(DOCUMENT_ID)].section_id)

    @app.get("/internal/v1/normative/sections/{selected_section}/documents")
    async def documents(selected_section: str):
        """Имитирует только список документов выбранного раздела, не корпус требований."""
        return [{"document_id": str(DOCUMENT_ID), "section_id": selected_section}]

    await catalog.record_status(
        document_id=DOCUMENT_ID, payload=proof_payload(), actor_id=ACTOR_ID
    )
    queued = await catalog.queue(document_id=DOCUMENT_ID)
    pages = (
        NormativeTextPage(
            1,
            "Учебный СП 99.13330.2026\n5.1 Для пожарных гидрантов расход должен быть не менее 5 л/с.",
        ),
    )
    await BuildNormativeRequirements(
        lambda: UnitOfWork(state), Storage(), Extractor(pages)
    ).execute(document_id=DOCUMENT_ID, generation_id=queued["generation_id"])
    text = f"Пожарный гидрант ПГ7. Расход: {value} л/с."
    fact = document_fact_from_mapping(
        {
            "kind": "parameter",
            "subject_type": "system",
            "identifier": "ПГ7",
            "property_type": "parameter",
            "property_name": "Расход",
            "value_raw": value,
            "unit_raw": "л/с",
            "evidence_text": text,
            "extraction_method": "pdf_text",
        }
    )

    async def no_cancel():
        """Контрольная точка без отмены."""

    independent = await IndependentNormativeReview(
        HttpNormativeRequirementsReader(
            "http://knowledge.test", transport=httpx.ASGITransport(app=app)
        )
    ).execute(
        document_id="control",
        section_id=section_id,
        document_ids=(str(DOCUMENT_ID),),
        pages=(DocumentPage(13, text, "", (fact,)),),
        assessed_on="2026-10-20",
        check_cancellation=no_cancel,
    )
    assert independent.diagnostics["coverage"]["source_complete"]
    assert independent.diagnostics["coverage"]["corpus_requirements"] > 0
    assert len(independent.findings) == expected

    class ForbiddenVision:
        """Доказанная программная проверка не требует нового обращения к VLM."""

        async def generate(self, **kwargs):
            """Попытка заменить детерминированную проверку моделью провалит тест."""
            pytest.fail("Незапланированный VLM-вызов")

    _, final, _ = await FinalizeFindings(ForbiddenVision(), 1000, 10, 1, 0.5).execute(
        findings=independent.findings, experience_by_finding={}
    )
    assert len(final) == expected
    if expected:
        source = final[0].basis_sources[0]
        assert source.atomic_requirement["clause"] == "5.1"
        assert "5 л/с" in source.atomic_requirement["source_text"]
        assert (
            source.atomic_requirement["source_sha256"]
            == state.documents[str(DOCUMENT_ID)].sha256
        )


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
