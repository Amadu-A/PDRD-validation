# services/analysis-service/tests/integration/test_engineering_program_http.py

"""Действующие HTTP-этапы сохраняют программный вывод, страницы и рамки без новых VLM."""

from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
)
from pdrd_analysis_service.application.use_cases.finalization import FinalizeFindings
from pdrd_analysis_service.application.use_cases.finding_localization import (
    LocalizeFindings,
)
from pdrd_analysis_service.domain.engineering_assertion import read_assertion
from pdrd_analysis_service.transport.http.document_context_routes import _options

from .test_http_api import FakeVisionModel, build_app, encoded_image, facts_payload


class ForbiddenVision:
    """Ни новый расчёт, ни ссылка, ни отсутствие bbox не должны обращаться к модели."""

    async def generate_json(self, **request):
        """Проверяет нулевой бюджет на действующей HTTP-границе."""
        raise AssertionError("Запрещён дополнительный VLM-вызов")


class ElevationVision(FakeVisionModel):
    """Воспроизводит найденную моделью претензию к отметке без обращения к GPU."""

    def __init__(self):
        """Учитывает физические вызовы замещённой модели по страницам."""
        self.calls = []

    async def generate_json(self, **request):
        """Сохраняет кандидат и исходную область независимо от наличия подходящего факта."""
        self.calls.append(request["stage"])
        generation = await super().generate_json(**request)
        candidate = {
            "category": "document_consistency",
            "severity": "warning",
            "status": "needs_review",
            "claim_type": "contradiction",
            "comment": "Абсолютная отметка узла K7 различается между представлениями.",
            "evidence": "Абсолютная отметка K7: +192,40 м.",
            "recommendation_draft": "",
            "confidence": 0.8,
            "normative_source_ids": [],
            "hypothesis_ids": ["engineering-1"],
            "object_ref": "K7",
            "visual_regions": [{"x_min": 10, "y_min": 20, "x_max": 100, "y_max": 50}],
        }
        return replace(
            generation,
            payload={"summary": "Проверка отметки", "violations": [candidate]},
        )


class DocumentCoverageVision(ElevationVision):
    """Проверяет сохранность пакета, а не способность модели самостоятельно находить ошибки."""

    async def generate_json(self, **request):
        """Возвращает по одному кандидату с разными N/T/U на каждой физической странице."""
        generation = await super().generate_json(**request)
        page = int(request["stage"].split(":")[1])
        candidate = dict(generation.payload["violations"][0])
        if page != 10:
            candidate.update(
                claim_type="documentation",
                comment=f"Уточнить маркировку узла K{page}.",
                evidence=f"Лист {page}: маркировка узла K{page}.",
                object_ref=f"K{page}",
                hypothesis_ids=[],
            )
        candidate.update(
            normative_source_ids=[f"N{page}"],
            technical_assignment_source_ids=[f"T{page}"],
            user_package_source_ids=[f"U{page}"],
        )
        return replace(
            generation,
            payload={"summary": f"Проверен лист {page}", "violations": [candidate]},
        )


@pytest.mark.parametrize("enabled", [True, False])
async def test_check_norms_all_31_pages_keep_facts_candidates_and_ntu_with_one_elevation(
    enabled,
):
    """Одна отметка не обрушивает весь пакет и не отбрасывает остальные 30 страниц."""
    app, model = build_app(), DocumentCoverageVision()
    app.state.container = replace(
        app.state.container,
        check_page_against_norms=replace(
            app.state.container.check_page_against_norms,
            vision_model=model,
            requirement_plan_enabled=enabled,
        ),
    )
    region = {"x_min": 10, "y_min": 20, "x_max": 100, "y_max": 50}
    items = []
    for page in range(1, 32):
        tag = "K7" if page == 10 else f"K{page}"
        text = (
            "Абсолютная отметка K7: +192,40 м."
            if page == 10
            else f"Лист {page}: маркировка узла {tag}."
        )
        fact = {
            "kind": "parameter" if page == 10 else "identifier",
            "subject_type": "node",
            "identifier": tag,
            "property_type": "parameter" if page == 10 else "identifier",
            "property_name": "Абсолютная отметка" if page == 10 else "Маркировка",
            "value_raw": "+192,40" if page == 10 else tag,
            "unit_raw": "м" if page == 10 else "",
            "evidence_text": text,
            "source_pages": [page],
            "source_regions": [region],
        }
        item = {
            "page_number": page,
            "extracted_text": text,
            "page_facts": {**facts_payload(), "document_facts": [fact]},
            "image_base64": encoded_image(),
        }
        for kind, field in (
            ("N", "normative_sources"),
            ("T", "technical_assignment_sources"),
            ("U", "user_package_sources"),
        ):
            item[field] = [
                {
                    "source_id": f"{kind}{page}",
                    "point_id": f"point-{kind}-{page}",
                    "score": 0.8,
                    "source_file": f"test-{kind}.pdf",
                    "page": page + 40,
                    "text": f"Стендовый источник {kind} для страницы {page}.",
                }
            ]
        items.append(item)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/v1/stages/check-norms",
            json={"document_id": str(uuid4()), "items": items},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cancelled"] is False
    assert [item["page_number"] for item in body["items"]] == list(range(1, 32))
    assert sorted(model.calls) == sorted(
        f"normative_check:{page}:probe1" for page in range(1, 32)
    )
    for original, result in zip(items, body["items"], strict=True):
        page = original["page_number"]
        finding = next(
            f for f in result["result"]["findings"] if f["finding_id"] == f"p{page}-f1"
        )
        assert (
            finding["page"] == page
            and finding["evidence"] == original["extracted_text"]
        )
        assert finding["visual_regions"][0].items() >= region.items()
        context = next(
            origin["context"]
            for origin in finding["origin_assertions"]
            if origin.get("origin") == "engineering_context"
        )
        source_fact = original["page_facts"]["document_facts"][0]
        assert context["page"] == page and len(context["facts"]) == 1
        for key, value in source_fact.items():
            assert context["facts"][0][key] == value
        for input_field, ids_field, saved_field in (
            ("normative_sources", "normative_source_ids", "basis_sources"),
            (
                "technical_assignment_sources",
                "technical_assignment_source_ids",
                "technical_assignment_basis_sources",
            ),
            (
                "user_package_sources",
                "user_package_source_ids",
                "user_package_basis_sources",
            ),
        ):
            source = original[input_field][0]
            assert finding[ids_field] == [source["source_id"]]
            assert len(finding[saved_field]) == 1
            for key, value in source.items():
                assert finding[saved_field][0][key] == value


async def test_check_norms_does_not_mask_unexpected_programming_error(monkeypatch):
    """Непредвиденное исключение не подменяется успешным пустым или частичным результатом."""
    app = build_app()

    def broken_binding(*args, **kwargs):
        """Моделирует независимый программный дефект внутри привязки кандидата."""
        raise RuntimeError("Непредвиденный дефект привязки")

    monkeypatch.setattr(
        "pdrd_analysis_service.application.use_cases.normative.bind_candidate_assertion",
        broken_binding,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/internal/v1/stages/check-norms",
            json={
                "document_id": str(uuid4()),
                "items": [
                    {
                        "page_number": 1,
                        "extracted_text": "Тестовая страница",
                        "page_facts": facts_payload(),
                        "normative_sources": [],
                        "image_base64": encoded_image(),
                    }
                ],
            },
        )
    assert response.status_code == 500
    assert response.text == "Internal Server Error"


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("batch", [True, False])
async def test_check_norms_preserves_elevation_candidate_and_only_binds_matching_fact(
    enabled, batch
):
    """Одиночный и пакетный HTTP-вызовы сохраняют замечание, область и точный предмет проверки."""
    app, model = build_app(), ElevationVision()
    app.state.container = replace(
        app.state.container,
        check_page_against_norms=replace(
            app.state.container.check_page_against_norms,
            vision_model=model,
            requirement_plan_enabled=enabled,
        ),
    )
    items = [
        {
            "page_number": page,
            "extracted_text": "Абсолютная отметка K7: +192,40 м.",
            "page_facts": {
                **facts_payload(),
                "document_facts": [
                    {
                        "kind": "parameter",
                        "subject_type": "node",
                        "identifier": "K7",
                        "property_type": "parameter",
                        "property_name": prop,
                        "value_raw": "+192,40",
                        "unit_raw": "м",
                        "evidence_text": "Абсолютная отметка K7: +192,40 м.",
                    }
                ],
            },
            "normative_sources": [],
            "image_base64": encoded_image(),
        }
        for page, prop in ((10, "Абсолютная отметка"), (11, "Длина"))
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        if batch:
            response = await client.post(
                "/internal/v1/stages/check-norms",
                json={"document_id": str(uuid4()), "items": items},
            )
            assert response.status_code == 200, response.text
            assert response.json()["cancelled"] is False
            assert [item["page_number"] for item in response.json()["items"]] == [
                10,
                11,
            ]
            results = [item["result"] for item in response.json()["items"]]
        else:
            results = []
            for item in items:
                response = await client.post(
                    "/internal/v1/pages/check-norms", json=item
                )
                assert response.status_code == 200, response.text
                results.append(response.json())
    assert model.calls == ["normative_check:10:probe1", "normative_check:11:probe1"]
    for page, result in zip((10, 11), results, strict=True):
        finding = next(
            f for f in result["findings"] if f["finding_id"] == f"p{page}-f1"
        )
        assert (
            finding["comment"]
            == "Абсолютная отметка узла K7 различается между представлениями."
        )
        assert finding["evidence"] == "Абсолютная отметка K7: +192,40 м."
        assert finding["visual_regions"][0]["x_min"] == 10
        assert finding["status"] in {"hypothesis", "needs_review"}
        assertion = read_assertion(tuple(finding["origin_assertions"]))
        if page == 10:
            assert assertion is not None and assertion.verification == "unverified"
            assert assertion.observations[0].page == page
            assert assertion.observations[0].fact.property_name == "Абсолютная отметка"
            assert assertion.observations[0].fact.value_raw == "+192,40"
        else:
            assert assertion is None


@pytest.mark.parametrize("enabled", [True, False])
async def test_full_http_program_evaluation_has_no_new_calls_or_contract_fields(
    enabled,
):
    """Числовое равенство из исходного текста доходит до final finding с теми же областями."""
    app, model = build_app(), ForbiddenVision()
    app.state.container = replace(
        app.state.container,
        check_cross_page_consistency=CheckCrossPageConsistency(
            model, _options(app.state.container)
        ),
        finalize_findings=FinalizeFindings(model, 2000, 8, 1000, 0.5),
        localize_findings=LocalizeFindings(model, 1000),
    )
    body = {
        "document_id": str(uuid4()),
        "enabled": enabled,
        "pages": [
            {
                "page_number": 1,
                "extracted_text": "2+3=6 м³/ч",
                "page_facts": {**facts_payload(), "document_facts": []},
                "text_words": [
                    {
                        "text": "2+3=6 м³/ч",
                        "bbox": {"x_min": 10, "y_min": 20, "x_max": 100, "y_max": 50},
                    }
                ],
            }
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        cross = await client.post(
            "/internal/v1/document-context/check-consistency", json=body
        )
        assert cross.status_code == 200, cross.text
        if not enabled:
            assert cross.json()["findings"] == []
            return
        findings = cross.json()["findings"]
        assert len(findings) == 1 and findings[0]["status"] == "confirmed"
        assert findings[0]["visual_regions"][0]["x_min"] == 10
        assert findings[0]["evidence_locations"][0]["page"] == 1
        response = await client.post(
            "/internal/v1/findings/finalize",
            json={"findings": findings, "experience_by_finding": {}},
        )
        assert response.status_code == 200, response.text
        assert response.json()["metrics"]["vlm_call_count"] == 0
        final = response.json()["findings"][0]
        assert final["visual_regions"] == findings[0]["visual_regions"]
        assert (
            final["document_context_basis_sources"]
            == findings[0]["document_context_basis_sources"]
        )
        localized = await client.post(
            "/internal/v1/findings/localize",
            json={
                "page_number": 1,
                "extracted_text": "2+3=6 м³/ч",
                "image_base64": encoded_image(),
                "findings": [
                    {key: final[key] for key in ("finding_id", "comment", "evidence")}
                ],
            },
        )
        assert localized.status_code == 200, localized.text
        assert localized.json()["metrics"]["attempt"] == 0
