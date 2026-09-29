# tests/functional/test_reviewed_pdf_api.py

"""Gateway → Experience → Document Service: настоящий PDF, решения, области и кеш."""

import hashlib
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import fitz
import pytest
from pdrd_api_gateway.application.use_cases.get_reviewed_pdf import GetReviewedPdf
from pdrd_api_gateway.core.settings import DocumentServiceSettings
from pdrd_api_gateway.infrastructure.analysis_pdf_export import (
    DocumentServiceAnalysisAnnotatedPdfRenderer,
)
from pdrd_api_gateway.infrastructure.reviewed_pdf_source import HttpReviewedPdfSource
from pdrd_api_gateway.infrastructure.storage.reviewed_pdf_cache import (
    LocalReviewedPdfCache,
)
from pdrd_api_gateway.main import create_app as gateway_app
from pdrd_document_service.application.use_cases.annotate import BuildAnnotatedPdf
from pdrd_document_service.core.container import (
    ApplicationContainer as DocumentContainer,
)
from pdrd_document_service.core.settings import Settings as DocumentSettings
from pdrd_document_service.infrastructure.pdf.annotator import PyMuPdfAnnotationWriter
from pdrd_document_service.main import create_app as document_app
from pdrd_experience_service.application.use_cases.confirm_areas import (
    ConfirmArea,
    RevokeArea,
)
from pdrd_experience_service.application.use_cases.export_review import ExportReview
from pdrd_experience_service.application.use_cases.review import ChangeReview
from pdrd_experience_service.main import create_app as experience_app

from tests.functional.reviewed_pdf_support import (
    MemoryAreas,
    MemoryReviewConfirmationCommitter,
)
from tests.functional.test_review_api import BOX, CARD, client, command
from tests.functional.test_review_api import flow as flow


@pytest.fixture
def pdf_flow(flow, tmp_path):
    """Расширяет действующий сквозной Review реальным PDF renderer и кешем."""
    with fitz.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_text((40, 40), "ORIGINAL DRAWING")
        original = document.tobytes()

    async def load_request(*, document_id):
        """Отдаёт один проверяемый оригинальный PDF для всех трёх сервисов."""
        return SimpleNamespace(
            submission=SimpleNamespace(
                document_id=document_id, pdf_file_name="План.pdf"
            ),
            pdf_content=original,
        )

    artifacts = flow.gateway.get_review_source.artifacts
    artifacts.load_request = load_request
    areas = MemoryAreas(flow.reviews)
    experience = replace(
        flow.experience,
        confirmed_areas=areas,
        change_review=ChangeReview(
            flow.reviews, MemoryReviewConfirmationCommitter(flow.reviews, areas)
        ),
        confirm_area=ConfirmArea(flow.reviews, areas),
        revoke_area=RevokeArea(flow.reviews, areas),
        export_review=ExportReview(flow.reviews, areas),
    )
    renderer = DocumentServiceAnalysisAnnotatedPdfRenderer(
        settings=DocumentServiceSettings(base_url="http://document"),
        transport=flow.network,
    )
    manage = flow.gateway.manage_review
    exporter = GetReviewedPdf(
        contexts=manage.contexts,
        access=manage.access,
        jobs=flow.jobs,
        source=HttpReviewedPdfSource(manage.service),
        artifacts=artifacts,
        renderer=renderer,
        cache=LocalReviewedPdfCache(tmp_path),
    )
    gateway = replace(flow.gateway, get_reviewed_pdf=exporter)
    document = DocumentContainer(
        settings=DocumentSettings(_env_file=None),
        extract_pdf=None,
        extract_cad=None,
        extract_combined=None,
        build_annotated_pdf=BuildAnnotatedPdf(PyMuPdfAnnotationWriter(), 10_000_000),
    )
    flow.network.apps.update(
        gateway=gateway_app(gateway),
        experience=experience_app(experience),
        document=document_app(document),
    )
    flow.areas, flow.exporter, flow.artifacts, flow.original = (
        areas,
        exporter,
        artifacts,
        original,
    )
    flow.pdf_endpoint = f"/api/v1/analyses/{flow.job_id}/reviewed-pdf"
    return flow


async def prepare(browser, flow, *, reject_first=False):
    """Рассматривает обе VLM-находки и добавляет принятый дробный Gold."""
    response = await browser.post(flow.endpoint + "/open")
    assert response.status_code == 200, response.text
    revision = response.json()["revision"]
    if reject_first:
        response = await command(
            browser,
            flow,
            revision,
            action="decide",
            finding_id="vlm:1",
            decision="accepted",
        )
        assert response.status_code == 200, response.text
        assert response.json()["area_confirmations"][0]["valid"]
        revision = response.json()["revision"]
    bodies = [
        {
            "action": "decide",
            "finding_id": "vlm:1",
            "decision": "rejected" if reject_first else "accepted",
        },
        {"action": "decide", "finding_id": "vlm:2", "decision": "accepted"},
        {
            "action": "add",
            "finding_id": "manual:00000000-0000-4000-8000-000000000001",
            "page_number": 1,
            "text": "Ручное замечание Gold: обозначение отсутствует.",
            "normative_basis": "СП 1, п. 3",
            "issue_box": {**BOX, "x_min": 10.25},
            "callout_box": CARD,
        },
        {
            "action": "decide",
            "finding_id": "manual:00000000-0000-4000-8000-000000000001",
            "decision": "accepted",
        },
        {"action": "approve"},
    ]
    for body in bodies:
        response = await command(browser, flow, revision, **body)
        assert response.status_code == 200, response.text
        revision = response.json()["revision"]
    return revision


async def pdf(browser, flow, revision):
    """Скачивает только по текущей серверной ревизии."""
    return await browser.post(flow.pdf_endpoint, json={"expected_revision": revision})


@pytest.mark.parametrize("reject_first", [False, True])
async def test_final_pdf_includes_gold_and_text_only_and_excludes_rejected(
    pdf_flow, reject_first
):
    """Полный русский текст, Gold на листе и отсутствие придуманных рамок unlocated."""
    f = pdf_flow
    async with client(f) as browser:
        revision = await prepare(browser, f, reject_first=reject_first)
        response = await pdf(browser, f, revision)
        assert response.status_code == 200, (
            response.text if response.status_code != 200 else ""
        )
        assert response.headers["cache-control"] == "no-store"
        assert "_reviewed.pdf" in response.headers["content-disposition"]
        with fitz.open(stream=response.content, filetype="pdf") as document:
            page = document[0]
            help_notes = [
                annot.info["content"]
                for annot in page.annots()
                if annot.type[1] == "Text"
            ]
            assert len(help_notes) == (1 if reject_first else 2)
            assert any("Ручное замечание Gold" in note for note in help_notes)
            assert all("Замечание без координат" not in note for note in help_notes)
            text = "\n".join(document[i].get_text() for i in range(1, len(document)))
            assert "Замечание без координат" in text
            assert "Ручное замечание Gold" in text
            assert ("Исходный полный текст VLM" in text) is not reject_first
        repeated = await pdf(browser, f, revision)
        assert repeated.content == response.content


async def test_pdf_blocked_until_all_decisions_and_current_approval(pdf_flow):
    """Отсутствие утверждения, устаревшая вкладка и новая правка дают 409."""
    f = pdf_flow
    async with client(f) as browser:
        await browser.post(f.endpoint + "/open")
        assert (await pdf(browser, f, 0)).status_code == 409
        revision = await prepare(browser, f)
        assert (await pdf(browser, f, revision - 1)).status_code == 409
        edited = await command(
            browser,
            f,
            revision,
            action="edit",
            finding_id="vlm:1",
            text="Новая правка",
            normative_basis="",
        )
        assert edited.status_code == 200
        assert not edited.json()["area_confirmations"][0]["valid"]
        assert (await pdf(browser, f, revision)).status_code == 409


async def test_revoking_area_invalidates_cache_even_with_same_approved_revision(
    pdf_flow,
):
    """Отозванная VLM-область исчезает с листа, полный текст остаётся в приложении."""
    f = pdf_flow
    async with client(f) as browser:
        revision = await prepare(browser, f)
        initial = await pdf(browser, f, revision)
        revoked = await command(
            browser,
            f,
            revision,
            action="revoke_area",
            finding_id="vlm:1",
            expected_confirmation_revision=1,
            reason="Область не соответствует замечанию",
        )
        assert revoked.status_code == 200
        assert revoked.json()["revision"] == revision
        result = await pdf(browser, f, revision)
        assert result.status_code == 200
        assert result.content != initial.content
        with fitz.open(stream=result.content, filetype="pdf") as document:
            page = document[0]
            assert len([note for note in page.annots() if note.type[1] == "Text"]) == 1
            assert "Исходный полный текст VLM" in document[1].get_text()


async def test_browser_cannot_forge_proposed_confirmation_or_export_payload(pdf_flow):
    """Координаты требуют явной проверки, ID/инженер/теги не принимаются из браузера."""
    f = pdf_flow
    async with client(f) as browser:
        await browser.post(f.endpoint + "/open")
        invalid = await command(
            browser,
            f,
            0,
            action="confirm_area",
            finding_id="vlm:1",
            expected_confirmation_revision=0,
            regions=[CARD],
            mode="proposed",
            note="",
        )
        assert invalid.status_code == 422
        invalid = await command(
            browser,
            f,
            0,
            action="confirm_area",
            finding_id="vlm:1",
            expected_confirmation_revision=0,
            regions=[CARD],
            mode="redrawn",
            note="",
        )
        assert invalid.status_code == 422
        invalid = await browser.post(
            f.pdf_endpoint, json={"expected_revision": 0, "actor": "forged"}
        )
        assert invalid.status_code == 422
        stranger = await browser.post(
            f"/api/v1/analyses/{uuid4()}/reviewed-pdf", json={"expected_revision": 0}
        )
        assert stranger.status_code == 404
        browser.headers.pop("X-PDRD-Review-Key")
        assert (await pdf(browser, f, 0)).status_code == 403


async def test_source_pdf_replacement_is_rejected_on_cache_hit(pdf_flow):
    """Кеш никогда не маскирует подмену исходника утверждённого Review."""
    f = pdf_flow
    async with client(f) as browser:
        revision = await prepare(browser, f)
        assert (await pdf(browser, f, revision)).status_code == 200

        async def replaced(*, document_id):
            """Имитирует изменение файла без изменения document_id."""
            return SimpleNamespace(
                submission=SimpleNamespace(document_id=document_id),
                pdf_content=b"%PDF-other",
            )

        f.artifacts.load_request = replaced
        assert (await pdf(browser, f, revision)).status_code == 409
        assert (
            hashlib.sha256(f.original).hexdigest()
            == f.reviews.sessions[f.job_id].source_sha256
        )


async def test_unlocated_vlm_is_only_text_without_artificial_geometry(pdf_flow):
    """Accept принимает имеющиеся рамки; замечание без области остаётся текстом."""
    f = pdf_flow
    async with client(f) as browser:
        revision = await prepare(browser, f)
        response = await pdf(browser, f, revision)
        assert response.status_code == 200
        with fitz.open(stream=response.content, filetype="pdf") as document:
            page = document[0]
            notes = [
                note.info["content"] for note in page.annots() if note.type[1] == "Text"
            ]
            assert len(notes) == 2 and any("Gold" in note for note in notes)
            assert not any("Замечание без координат" in note for note in notes)
            assert "Исходный полный текст VLM" in document[1].get_text()
            assert "Замечание без координат" in document[1].get_text()


async def test_all_rejected_review_can_produce_pdf_with_empty_accepted_list(pdf_flow):
    """Решение отклонить все замечания тоже является завершённым Review."""
    f = pdf_flow
    async with client(f) as browser:
        await browser.post(f.endpoint + "/open")
        revision = 0
        for finding_id in ("vlm:1", "vlm:2"):
            response = await command(
                browser,
                f,
                revision,
                action="decide",
                finding_id=finding_id,
                decision="rejected",
            )
            assert response.status_code == 200
            revision = response.json()["revision"]
        approved = await command(browser, f, revision, action="approve")
        response = await pdf(browser, f, approved.json()["revision"])
        assert response.status_code == 200
        with fitz.open(stream=response.content, filetype="pdf") as document:
            page = document[0]
            assert not list(page.annots() or ())
            text = " ".join(document[1].get_text().split())
            assert "Принято замечаний: 0" in text
            assert "Исходный полный текст VLM" not in text
            assert "Замечание без координат" not in text


@pytest.mark.parametrize("cache_hit", [False, True])
@pytest.mark.parametrize("change", ["review", "area"])
async def test_parallel_changes_block_pdf_after_render_and_cache_hit(
    pdf_flow, cache_hit, change
):
    """Правка или отзыв во время формирования не выпускает устаревший PDF."""
    f = pdf_flow

    def mutate():
        """Имитирует успешно записанное действие другой вкладки."""
        if change == "review":
            from datetime import UTC, datetime

            current = f.reviews.sessions[f.job_id]
            f.reviews.sessions[f.job_id] = current.reset_decision(
                finding_id="vlm:1",
                actor="engineer:other",
                at=datetime.now(UTC),
                expected_revision=current.revision,
            )
        else:
            confirmation, revision, _ = f.areas.rows["vlm:1"]
            f.areas.rows["vlm:1"] = (confirmation, revision + 1, False)

    async with client(f) as browser:
        revision = await prepare(browser, f)
        exporter = f.exporter
        if cache_hit:
            assert (await pdf(browser, f, revision)).status_code == 200

            class RacingCache:
                """Изменяет состояние после попадания в уже проверенный кеш."""

                async def load(self, **kwargs):
                    """Возвращает старые байты, которые use case обязан отвергнуть."""
                    content = await exporter.cache.load(**kwargs)
                    assert content is not None
                    mutate()
                    return content

                async def save(self, **kwargs):
                    """Для попадания в кеш перезапись не ожидается."""
                    raise AssertionError("Unexpected cache write")

            raced = replace(exporter, cache=RacingCache())
        else:

            async def render(**kwargs):
                """Document Service формирует PDF одновременно с правкой Review."""
                result = await exporter.renderer.render(**kwargs)
                mutate()
                return result

            raced = replace(exporter, renderer=SimpleNamespace(render=render))
        application = f.network.apps["gateway"]
        application.state.container = replace(
            application.state.container, get_reviewed_pdf=raced
        )
        response = await pdf(browser, f, revision)
        assert response.status_code == 409
        assert not response.content.startswith(b"%PDF-")
