# tests/functional/test_experience_catalog_api.py

"""Gateway → Experience → Document: настоящие PNG, CRUD, фильтры и ZIP каталога."""

import io
import json
import zipfile
from dataclasses import replace
from uuid import UUID, uuid4

import fitz
import httpx
import pytest
from pdrd_api_gateway.application.use_cases.manage_experience import ManageExperience
from pdrd_api_gateway.infrastructure.experience import (
    ControlledExperienceAccess,
    ControlledExperienceContext,
    HttpExperienceService,
)
from pdrd_api_gateway.main import create_app as gateway_app
from pdrd_document_service.application.use_cases.crop_pdf import CropPdf
from pdrd_document_service.infrastructure.pdf.crop import PyMuPdfCropRenderer
from pdrd_document_service.main import create_app as document_app
from pdrd_experience_service.application.use_cases.approval_experience import (
    ApprovalExperience,
)
from pdrd_experience_service.application.use_cases.capture_experience import (
    CaptureExperience,
)
from pdrd_experience_service.application.use_cases.catalog import ManageCatalog
from pdrd_experience_service.application.use_cases.confirm_areas import (
    ConfirmArea,
    RevokeArea,
)
from pdrd_experience_service.application.use_cases.export_catalog import ExportCatalog
from pdrd_experience_service.infrastructure.analysis.http_source import (
    GatewayAnalysisSource,
)
from pdrd_experience_service.infrastructure.crops import (
    DocumentCropRenderer,
    LocalCropStore,
)
from pdrd_experience_service.main import create_app as experience_app

from tests.functional.catalog_support import CatalogAreas, MemoryCatalog
from tests.functional.test_review_api import INTERNAL_KEY, client, command
from tests.functional.test_review_api import flow as flow
from tests.functional.test_reviewed_pdf_api import pdf_flow as pdf_flow
from tests.functional.test_reviewed_pdf_api import prepare


@pytest.fixture
def catalog_flow(pdf_flow, tmp_path):
    """Реальный HTTP-контур с устойчивыми crop и подменённым только SQL-портом."""
    flow = pdf_flow
    areas = CatalogAreas(flow.reviews)
    catalog = MemoryCatalog(flow.reviews, areas)
    crops = LocalCropStore(tmp_path / "crops")
    renderer = DocumentCropRenderer("http://document", transport=flow.network)
    capture = CaptureExperience(
        flow.reviews,
        areas,
        GatewayAnalysisSource("http://gateway", INTERNAL_KEY, transport=flow.network),
        renderer,
        crops,
        catalog,
    )
    experience = replace(
        flow.network.apps["experience"].state.container,
        confirmed_areas=areas,
        confirm_area=ConfirmArea(flow.reviews, areas),
        revoke_area=RevokeArea(flow.reviews, areas),
        catalog=ManageCatalog(catalog, crops),
        export_catalog=ExportCatalog(catalog, crops),
        capture_experience=capture,
        approval_experience=ApprovalExperience(capture),
    )
    gateway = replace(
        flow.network.apps["gateway"].state.container,
        manage_experience=ManageExperience(
            ControlledExperienceContext("engineer:test"),
            ControlledExperienceAccess(
                "engineer:test", flow.gateway.manage_review.access
            ),
            HttpExperienceService(
                "http://experience", INTERNAL_KEY, transport=flow.network
            ),
        ),
    )
    document = replace(
        flow.network.apps["document"].state.container,
        crop_pdf=CropPdf(PyMuPdfCropRenderer(), 1_000_000),
    )
    flow.network.apps.update(
        gateway=gateway_app(gateway),
        experience=experience_app(experience),
        document=document_app(document),
    )
    flow.areas, flow.catalog, flow.capture, flow.crops, flow.crop_renderer = (
        areas,
        catalog,
        capture,
        crops,
        renderer,
    )
    return flow


async def listed(browser, **params):
    """Проверяет успешный ответ, чтобы 503 не маскировался пустой таблицей."""
    response = await browser.get("/api/v1/experience", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_lan_frontend_approval_saves_experience_and_exports_reviewed_pdf(
    catalog_flow,
):
    """Основной LAN Origin: pending блокирует PDF, Review сохраняет Bad/Gold и выводит только accepted."""
    flow = catalog_flow
    async with client(flow, base_url="http://192.168.55.3:8080") as browser:
        config = await browser.get("/api/v1/review/config")
        assert config.json() == {"enabled": True}
        await browser.post(flow.endpoint + "/open")
        assert (
            await browser.post(flow.pdf_endpoint, json={"expected_revision": 0})
        ).status_code == 409
        revision = await prepare(browser, flow, reject_first=True)
        review = (await browser.get(flow.endpoint)).json()
        assert review["pending_count"] == 0
        assert review["approved_revision"] == revision
        records = (await listed(browser))["items"]
        assert {record["tag"] for record in records} == {"bad", "gold"}
        response = await browser.post(
            flow.pdf_endpoint, json={"expected_revision": revision}
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        with fitz.open(stream=response.content, filetype="pdf") as document:
            notes = [
                annotation.info["content"]
                for annotation in document[0].annots()
                if annotation.type[1] == "Text"
            ]
            assert len(notes) == 1 and "Ручное замечание Gold" in notes[0]
            text = "\n".join(page.get_text() for page in document)
            assert "Ручное замечание Gold" in text
            assert "Замечание без координат" in text
            assert "Исходный полный текст VLM" not in text
        repeated = await browser.post(
            f"/api/v1/experience/capture/{flow.job_id}",
            json={"expected_revision": revision},
        )
        assert repeated.status_code == 200
        assert repeated.json()["created"] == 0
        assert (await listed(browser))["total"] == 2


async def test_approval_materializes_verified_examples_and_crop_survives_reload(
    catalog_flow,
):
    """Wise и Gold сохраняются автоматически; unlocated исключается из Experience."""
    flow = catalog_flow
    async with client(flow) as browser:
        revision = await prepare(browser, flow)
        rows = await listed(browser)
        assert rows["total"] == 2
        assert {item["tag"] for item in rows["items"]} == {"wise", "gold"}
        assert all(item["active"] and item["source_current"] for item in rows["items"])
        record = rows["items"][0]
        image = await browser.get(f"/api/v1/experience/{record['id']}/crops/0")
        assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
        assert image.headers["cache-control"] == "no-store"
        example = (await flow.catalog.get(UUID(record["id"]))).example
        assert (
            await LocalCropStore(flow.crops.root).read(example.crops[0])
            == image.content
        )
        repeated = await browser.post(
            f"/api/v1/experience/capture/{flow.job_id}",
            json={"expected_revision": revision},
        )
        assert repeated.status_code == 200 and repeated.json()["created"] == 0
        assert repeated.json()["eligible"] == 2 and repeated.json()["excluded"] == 1
        assert (await listed(browser))["total"] == 2


async def test_curate_cas_filters_and_history_preserve_gold_and_originals(catalog_flow):
    """Правка каталога не подменяет источник, а вторая вкладка получает 409."""
    flow = catalog_flow
    async with client(flow) as browser:
        await prepare(browser, flow)
        gold = (await listed(browser, tag="gold"))["items"][0]
        url = f"/api/v1/experience/{gold['id']}"
        body = {
            "expected_revision": 0,
            "fields": {
                "text": "=Новая полная формулировка",
                "active": False,
                "normative_reference": "СП 1 · раздел 3",
            },
        }
        response = await browser.patch(url, json=body)
        assert response.status_code == 200, response.text
        revised = response.json()
        assert revised["tag"] == "gold" and revised["source"] == gold["source"]
        assert not revised["active"] and revised["revision"] == 1
        assert (await browser.patch(url, json=body)).status_code == 409
        assert (await listed(browser, query="НОВАЯ", active="false"))["total"] == 1
        assert (await listed(browser, active="true"))["total"] == 1
        history = (await browser.get(url + "/history")).json()["events"]
        assert len(history) == 2 and history[0]["snapshot"]["text"] == gold["text"]
        assert history[1]["actor"] == "engineer:test"
        assert (await flow.reviews.load(flow.job_id)).findings[-1].text == gold["text"]


async def test_edited_rejected_requires_explicit_adjudication(catalog_flow):
    """Оба текста и отклонение сохраняются; negative появляется только после уточнения."""
    flow = catalog_flow
    async with client(flow) as browser:
        await prepare(browser, flow, reject_first=True)
        bad = (await listed(browser, tag="bad"))["items"][0]
        url = f"/api/v1/experience/{bad['id']}"
        response = await browser.patch(
            url, json={"expected_revision": 0, "fields": {"text": "Исправленный отказ"}}
        )
        assert response.status_code == 200
        assert (
            response.json()["tag"] == "edited"
            and response.json()["learning_use"] == "needs_adjudication"
        )
        assert not response.json()["training_eligible"]
        assert (await listed(browser, tag="edited:rejected", decision="accepted"))[
            "total"
        ] == 0
        response = await browser.patch(
            url,
            json={
                "expected_revision": 1,
                "fields": {
                    "rejection_reason": "Объект ошибки отсутствует",
                    "negative_target": "original",
                },
            },
        )
        assert (
            response.status_code == 200
            and response.json()["learning_use"] == "negative"
        )
        assert (
            response.json()["source"]["original_text"] == bad["source"]["original_text"]
        )


async def test_export_contains_all_filtered_examples_metadata_png_and_safe_csv(
    catalog_flow,
):
    """Размер страницы UI не ограничивает ZIP; полный текст и crop проверяются по bytes."""
    flow = catalog_flow
    async with client(flow) as browser:
        await prepare(browser, flow)
        assert len((await listed(browser, limit=1))["items"]) == 1
        response = await browser.get("/api/v1/experience/export", params={"limit": 1})
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["version"] == 1 and len(manifest["examples"]) == 2
            for example in manifest["examples"]:
                assert example["text"] and example["source"]["original_text"]
                assert archive.read(example["crop_files"][0]).startswith(b"\x89PNG")
            assert "Ручное замечание Gold" in archive.read("examples.csv").decode(
                "utf-8-sig"
            )


@pytest.mark.parametrize(
    "fields",
    [
        {"actor": "attacker"},
        {"tag": "gold"},
        {"source": {}},
        {"issue_regions": []},
        {"decision": "accepted"},
    ],
)
async def test_browser_cannot_forge_source_or_actor(catalog_flow, fields):
    """Оба HTTP-контракта запрещают серверные поля."""
    async with client(catalog_flow) as browser:
        await prepare(browser, catalog_flow)
        record = (await listed(browser))["items"][0]
        response = await browser.patch(
            f"/api/v1/experience/{record['id']}",
            json={"expected_revision": 0, "fields": fields},
        )
        assert response.status_code == 422


async def test_closed_channel_cross_site_unknown_job_and_unapproved_capture(
    catalog_flow,
):
    """Read/crop/export защищены так же, как команды Review."""
    flow = catalog_flow
    async with httpx.AsyncClient(
        transport=flow.network, base_url="http://gateway"
    ) as public:
        assert (await public.get("/api/v1/experience")).status_code == 403
        assert (await public.get("/api/v1/experience/export")).status_code == 403
    async with client(flow) as browser:
        assert (
            await browser.get(
                "/api/v1/experience", headers={"Origin": "https://foreign.example"}
            )
        ).status_code == 403
        assert (
            await browser.post(
                f"/api/v1/experience/capture/{uuid4()}", json={"expected_revision": 0}
            )
        ).status_code == 404
        await browser.post(flow.endpoint + "/open")
        assert (
            await browser.post(
                f"/api/v1/experience/capture/{flow.job_id}",
                json={"expected_revision": 0},
            )
        ).status_code == 409


async def test_revoked_area_immediately_marks_saved_example_inactive(catalog_flow):
    """Пример и PNG остаются в аудите, но больше не пригодны для обучения."""
    flow = catalog_flow
    async with client(flow) as browser:
        revision = await prepare(browser, flow)
        wise = (await listed(browser, tag="wise"))["items"][0]
        response = await command(
            browser,
            flow,
            revision,
            action="revoke_area",
            finding_id="vlm:1",
            expected_confirmation_revision=1,
            reason="Ошибка локализации",
        )
        assert response.status_code == 200
        entry = (await browser.get(f"/api/v1/experience/{wise['id']}")).json()
        assert (
            not entry["source_current"]
            and not entry["active"]
            and not entry["training_eligible"]
        )
        assert (await listed(browser, active="true"))["total"] == 1
        assert (
            await browser.get(f"/api/v1/experience/{wise['id']}/crops/0")
        ).status_code == 200
