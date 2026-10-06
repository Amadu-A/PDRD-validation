# tests/functional/test_experience_catalog_provenance.py

"""Исходная область Bad, правки инженера и текстовые решения через реальные HTTP/crop."""

import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import fitz
import pytest
from pdrd_experience_service.domain.review import Rectangle

from tests.functional.test_experience_catalog_api import catalog_flow as catalog_flow
from tests.functional.test_experience_catalog_api import listed
from tests.functional.test_review_api import BOX, client, command
from tests.functional.test_review_api import flow as flow
from tests.functional.test_reviewed_pdf_api import pdf_flow as pdf_flow


@pytest.mark.parametrize("moved", [False, True])
async def test_second_finding_keeps_both_original_regions_on_second_page(
    catalog_flow, moved
):
    """Отказ замечания №2 сохраняет экспликацию и план, не рамку карточки текста."""
    f = catalog_flow
    boxes = [
        {"x_min": 780, "y_min": 100, "x_max": 980, "y_max": 400},
        {"x_min": 100, "y_min": 100, "x_max": 700, "y_max": 700},
    ]
    comment = "В экспликации помещений указано 14 позиций, тогда как на плане обозначены только 13 помещений."
    with fitz.open() as document:
        document.new_page(width=595, height=842)
        page = document.new_page(width=842, height=595)
        page.insert_text((90, 100), "FLOOR PLAN")
        page.insert_text((670, 100), "ROOM SCHEDULE")
        original = document.tobytes()

    async def request(*, document_id):
        """Сервер отдаёт исходный двухстраничный PDF, а не аннотированный рендер."""
        return SimpleNamespace(
            submission=SimpleNamespace(
                document_id=document_id, pdf_file_name="План.pdf"
            ),
            pdf_content=original,
        )

    source = f.gateway.get_review_source
    read_result = source.artifacts.load_result
    read_visualization = source.visualizations.execute

    async def result(*, document_id):
        """Второе замечание относится к физическому листу 2."""
        payload = await read_result(document_id=document_id)
        payload["findings"][1].update(page=2, comment=comment)
        return payload

    async def visualization(*, job_id):
        """Обе области принадлежат одному исходному finding_id на одном листе."""
        payload = await read_visualization(job_id=job_id)
        payload["pages"].append(
            {
                "page_number": 2,
                "locations": [
                    {
                        "finding_id": "vlm:2",
                        "status": "located",
                        "method": "analysis_vlm",
                        "regions": [
                            {
                                "bbox": box,
                                "source": "analysis_vlm",
                                "confidence": 0.9 - index * 0.1,
                            }
                            for index, box in enumerate(boxes)
                        ],
                    }
                ],
            }
        )
        return payload

    source.artifacts.load_request = request
    source.artifacts.load_result = result
    source.visualizations.execute = visualization
    async with client(f) as browser:
        opened = await browser.post(f.endpoint + "/open")
        assert opened.status_code == 200, opened.text
        initial = opened.json()["findings"][1]
        assert [area["bbox"] for area in initial["proposed_regions"]] == boxes
        revision = opened.json()["revision"]
        changed = [{**boxes[0], "x_min": 750}, {**boxes[1], "x_max": 650}]
        if moved:
            response = await command(
                browser,
                f,
                revision,
                action="geometry",
                finding_id="vlm:2",
                regions=changed,
                callout_box=None,
            )
            assert response.status_code == 200, response.text
            revision = response.json()["revision"]
        for finding_id, decision in (("vlm:1", "accepted"), ("vlm:2", "rejected")):
            response = await command(
                browser,
                f,
                revision,
                action="decide",
                finding_id=finding_id,
                decision=decision,
                reason_category="false_positive" if decision == "rejected" else None,
            )
            assert response.status_code == 200, response.text
            revision = response.json()["revision"]
        assert (await listed(browser))["total"] == 0
        approved = await command(browser, f, revision, action="approve")
        assert approved.status_code == 200, approved.text
        assert approved.json()["experience_capture"]["status"] == "saved"
        bad = (await listed(browser, tag="bad"))["items"][0]
        assert bad["source"]["page_number"] == 2
        assert bad["source"]["original_text"] == comment
        assert bad["source"]["proposed_regions"] == initial["proposed_regions"]
        assert bad["source"]["issue_regions"] == boxes
        assert bad["source"]["display_regions"] == (changed if moved else None)
        assert bad["source"]["area_source"] == "vlm_original"
        assert bad["source"]["confirmed_by"] == ""
        assert bad["training_eligible"] and len(bad["crops"]) == 2
        images = await f.crop_renderer.render(
            pdf=original,
            page_number=2,
            regions=tuple(Rectangle(**box) for box in boxes),
        )
        for index, image in enumerate(images):
            saved = await browser.get(f"/api/v1/experience/{bad['id']}/crops/{index}")
            assert saved.status_code == 200, saved.text
            assert saved.content == image
            assert bad["crops"][index]["sha256"] == hashlib.sha256(image).hexdigest()
        archive = await browser.get("/api/v1/experience/export", params={"tag": "bad"})
        assert archive.status_code == 200, archive.text
        with zipfile.ZipFile(io.BytesIO(archive.content)) as exported:
            manifest = json.loads(exported.read("manifest.json"))
            assert len(manifest["examples"]) == 1
            assert manifest["examples"][0]["source"]["issue_regions"] == boxes
            for index, image in enumerate(images):
                assert exported.read(f"crops/{bad['id']}/{index}.png") == image
        repeated = await f.capture.execute(
            job_id=f.job_id,
            expected_revision=approved.json()["revision"],
            actor="engineer:test",
        )
        assert repeated["created"] == 0 and repeated["repaired"] == []
        assert (await listed(browser))["total"] == 2
        pdf = await browser.post(
            f.pdf_endpoint, json={"expected_revision": approved.json()["revision"]}
        )
        assert pdf.status_code == 200, pdf.text
        with fitz.open(stream=pdf.content, filetype="pdf") as document:
            assert list(document[1].annots()) == []
            text = " ".join(" ".join(page.get_text() for page in document).split())
            assert comment not in text
            assert "Исходный полный текст VLM" in text


@pytest.mark.parametrize("scenario", ["original", "moved", "unlocated"])
async def test_bad_keeps_vlm_source_and_engineer_geometry_separately(
    catalog_flow, scenario
):
    """Отказ не подтверждает правку и не подменяет исходный crop VLM."""
    f = catalog_flow
    async with client(f) as browser:
        await browser.post(f.endpoint + "/open")
        revision = 0
        changed = {"x_min": 400.0, "y_min": 400.0, "x_max": 800.0, "y_max": 900.0}
        if scenario == "moved":
            result = await command(
                browser,
                f,
                revision,
                action="geometry",
                finding_id="vlm:1",
                regions=[changed],
                callout_box=None,
            )
            assert result.status_code == 200, result.text
            revision = result.json()["revision"]
        target = "vlm:2" if scenario == "unlocated" else "vlm:1"
        for finding in ("vlm:1", "vlm:2"):
            result = await command(
                browser,
                f,
                revision,
                action="decide",
                finding_id=finding,
                decision="rejected" if finding == target else "accepted",
                reason_category="false_positive"
                if ("rejected" if finding == target else "accepted") == "rejected"
                else None,
            )
            assert result.status_code == 200, result.text
            revision = result.json()["revision"]
        assert (await listed(browser))["total"] == 0
        result = await command(browser, f, revision, action="approve")
        assert result.status_code == 200, result.text
        assert result.json()["experience_capture"]["status"] == "saved"
        bad = (await listed(browser, tag="bad"))["items"][0]
        assert bad["source"]["confirmed_by"] == ""
        if scenario == "unlocated":
            assert bad["source"]["proposed_regions"] == []
            assert bad["source"]["issue_regions"] == [] and bad["crops"] == []
            assert bad["source_current"] and not bad["training_eligible"]
        else:
            assert bad["source"]["issue_regions"] == [BOX]
            assert bad["source"]["proposed_regions"][0]["bbox"] == BOX
            assert bad["source"]["display_regions"] == (
                [changed] if scenario == "moved" else None
            )
            assert bad["training_eligible"]
            original = await f.capture.source.load_completed(f.job_id)
            png = (
                await f.crop_renderer.render(
                    pdf=original.pdf_content, page_number=1, regions=(Rectangle(**BOX),)
                )
            )[0]
            assert bad["crops"][0]["sha256"] == hashlib.sha256(png).hexdigest()
        repeated = await f.capture.execute(
            job_id=f.job_id,
            expected_revision=result.json()["revision"],
            actor="engineer:test",
        )
        assert repeated["created"] == 0 and repeated["repaired"] == []
        assert (await listed(browser))["total"] == 2


async def test_undone_rejection_is_not_in_final_catalog(catalog_flow):
    """Журнал хранит отказ и отмену, каталог — только окончательное принятие."""
    f = catalog_flow
    async with client(f) as browser:
        await browser.post(f.endpoint + "/open")
        revision = 0
        for body in (
            {
                "action": "decide",
                "finding_id": "vlm:1",
                "decision": "rejected",
                "reason_category": "false_positive",
            },
            {"action": "reset", "finding_id": "vlm:1"},
            {"action": "decide", "finding_id": "vlm:1", "decision": "accepted"},
            {"action": "decide", "finding_id": "vlm:2", "decision": "accepted"},
            {"action": "approve"},
        ):
            result = await command(browser, f, revision, **body)
            assert result.status_code == 200, result.text
            revision = result.json()["revision"]
        assert (await listed(browser, tag="bad"))["total"] == 0
        assert (await listed(browser, tag="wise"))["total"] == 2


async def test_rejected_gold_is_saved_in_catalog_without_automatic_training(
    catalog_flow,
):
    """Окончательный отказ ручного замечания также входит в согласованный каталог."""
    f = catalog_flow
    async with client(f) as browser:
        await browser.post(f.endpoint + "/open")
        revision = 0
        manual_id = "manual:00000000-0000-4000-8000-000000000001"
        for body in (
            {"action": "decide", "finding_id": "vlm:1", "decision": "accepted"},
            {"action": "decide", "finding_id": "vlm:2", "decision": "accepted"},
            {
                "action": "add",
                "finding_id": manual_id,
                "page_number": 1,
                "text": "Ошибочное ручное замечание",
                "normative_basis": "",
                "issue_box": BOX,
                "callout_box": {**BOX, "x_min": 400.0, "x_max": 800.0},
            },
            {
                "action": "decide",
                "finding_id": manual_id,
                "decision": "rejected",
                "reason_category": "false_positive",
            },
            {"action": "approve"},
        ):
            result = await command(browser, f, revision, **body)
            assert result.status_code == 200, result.text
            revision = result.json()["revision"]
        gold = (await listed(browser, tag="gold"))["items"][0]
        assert gold["decision"] == "rejected" and gold["crops"]
        assert gold["source_current"] and not gold["training_eligible"]
