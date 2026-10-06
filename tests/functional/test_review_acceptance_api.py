# tests/functional/test_review_acceptance_api.py

"""Одна зелёная галочка: HTTP → атомарный Review → crop Experience → итоговый PDF.

Проверяет пользовательские сценарии без отдельной команды подтверждения области.
Настоящая конкуренция транзакций и откаты дополнительно проверяются PostgreSQL.
"""

from uuid import UUID

import fitz
import pytest

from tests.functional.test_experience_catalog_api import catalog_flow as catalog_flow
from tests.functional.test_experience_catalog_api import listed
from tests.functional.test_review_api import BOX, client, command
from tests.functional.test_review_api import flow as flow
from tests.functional.test_reviewed_pdf_api import pdf_flow as pdf_flow

FIXED = {**BOX, "x_min": 15.25, "x_max": 140.75}


@pytest.mark.parametrize(
    ("actions", "tag", "learning_use", "regions"),
    [
        (("accepted",), "wise", "positive", BOX),
        (("geometry", "accepted"), "wise", "positive", FIXED),
        (("rejected",), "bad", "negative", BOX),
        (("accepted", "rejected"), "bad", "negative", BOX),
        (("accepted", "geometry", "rejected"), "bad", "negative", BOX),
        (("edit", "accepted"), "edited", "positive", BOX),
        (("edit", "accepted", "rejected"), "edited", "needs_adjudication", BOX),
        (("accepted", "edit", "rejected"), "edited", "needs_adjudication", BOX),
    ],
)
async def test_acceptance_geometry_rejection_and_training_selection(
    catalog_flow, actions, tag, learning_use, regions
):
    """Bad сохраняет первоначальную область; Accept принимает текущую; текстовые решения остаются в каталоге."""
    flow = catalog_flow
    async with client(flow, base_url="http://192.168.55.3:8080") as browser:
        opened = await browser.post(flow.endpoint + "/open")
        assert opened.status_code == 200
        revision = opened.json()["revision"]
        for action in actions:
            body = {"action": "decide", "decision": action}
            if action == "rejected":
                body["reason_category"] = "false_positive"
            if action == "geometry":
                body = {"action": "geometry", "regions": [FIXED], "callout_box": None}
            elif action == "edit":
                body = {
                    "action": "edit",
                    "text": "Исправленный текст",
                    "normative_basis": "СП 2",
                }
            response = await command(
                browser, flow, revision, finding_id="vlm:1", **body
            )
            assert response.status_code == 200, response.text
            revision = response.json()["revision"]
            statuses = response.json()["area_confirmations"]
            if action == "accepted":
                assert len(statuses) == 1 and statuses[0]["valid"]
            elif action in ("geometry", "edit"):
                assert not any(status["valid"] for status in statuses)
        response = await command(
            browser,
            flow,
            revision,
            action="decide",
            finding_id="vlm:2",
            decision="accepted",
        )
        assert response.status_code == 200
        response = await command(
            browser, flow, response.json()["revision"], action="approve"
        )
        assert response.status_code == 200, response.text
        review = response.json()
        assert review["experience_capture"]["eligible"] == (1 if tag else 0)
        restored = (await browser.get(flow.endpoint)).json()
        assert restored["findings"] == review["findings"]
        records = (await listed(browser))["items"]
        assert len(records) == 2
        if not tag:
            assert records[0]["tag"] == ("edited" if "edit" in actions else "bad")
            assert records[0]["crops"] == [] and not records[0]["training_eligible"]
            assert records[0]["source"]["issue_regions"] == []
        if tag:
            record = next(
                item for item in records if item["source"]["finding_id"] == "vlm:1"
            )
            assert record["tag"] == tag and record["learning_use"] == learning_use
            example = (await flow.catalog.get(UUID(record["id"]))).example
            assert example.source.issue_regions[0].x_min == regions["x_min"]
            assert example.source.confirmed_by == (
                "engineer:test" if actions[-1] == "accepted" else ""
            )
            image = await browser.get(f"/api/v1/experience/{record['id']}/crops/0")
            assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
        document_response = await browser.post(
            flow.pdf_endpoint, json={"expected_revision": review["revision"]}
        )
        assert document_response.status_code == 200, document_response.text
        with fitz.open(stream=document_response.content, filetype="pdf") as document:
            notes = [
                note.info["content"]
                for note in document[0].annots() or ()
                if note.type[1] == "Text"
            ]
            assert len(notes) == (1 if actions[-1] == "accepted" else 0)
            text = "\n".join(page.get_text() for page in document)
            assert "Замечание без координат" in text
            if actions[-1] == "rejected":
                assert "Исходный полный текст VLM" not in text
                assert "Исправленный текст" not in text
