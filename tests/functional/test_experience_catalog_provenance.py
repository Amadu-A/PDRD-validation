# tests/functional/test_experience_catalog_provenance.py

"""Исходная область Bad, правки инженера и текстовые решения через реальные HTTP/crop."""

import hashlib

import pytest
from pdrd_experience_service.domain.review import Rectangle

from tests.functional.test_experience_catalog_api import catalog_flow as catalog_flow
from tests.functional.test_experience_catalog_api import listed
from tests.functional.test_review_api import BOX, client, command
from tests.functional.test_review_api import flow as flow
from tests.functional.test_reviewed_pdf_api import pdf_flow as pdf_flow


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
            {"action": "decide", "finding_id": "vlm:1", "decision": "rejected"},
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
            {"action": "decide", "finding_id": manual_id, "decision": "rejected"},
            {"action": "approve"},
        ):
            result = await command(browser, f, revision, **body)
            assert result.status_code == 200, result.text
            revision = result.json()["revision"]
        gold = (await listed(browser, tag="gold"))["items"][0]
        assert gold["decision"] == "rejected" and gold["crops"]
        assert gold["source_current"] and not gold["training_eligible"]
