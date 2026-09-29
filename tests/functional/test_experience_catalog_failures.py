# tests/functional/test_experience_catalog_failures.py

"""Ошибки переноса и выгрузки не теряют Review и не создают ложные примеры."""

import csv
import io
import json
import zipfile
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from pdrd_experience_service.infrastructure.crops import (
    DocumentCropRenderer,
    LocalCropStore,
)

from tests.functional.test_experience_catalog_api import catalog_flow as catalog_flow
from tests.functional.test_experience_catalog_api import listed
from tests.functional.test_review_api import client, command
from tests.functional.test_review_api import flow as flow
from tests.functional.test_reviewed_pdf_api import pdf_flow as pdf_flow
from tests.functional.test_reviewed_pdf_api import prepare


async def test_failed_capture_acknowledges_approved_review_and_can_retry(
    catalog_flow, monkeypatch
):
    """Сбой Document не превращает выполненное утверждение в потерянную команду."""
    f = catalog_flow
    original = DocumentCropRenderer.render

    async def unavailable(self, **kwargs):
        raise RuntimeError("private storage failure")

    monkeypatch.setattr(DocumentCropRenderer, "render", unavailable)
    async with client(f) as browser:
        revision = await prepare(browser, f)
        response = await command(browser, f, revision, action="approve")
        assert response.status_code == 200
        assert response.json()["approved_revision"] == revision
        assert response.json()["experience_capture"]["status"] == "error"
        assert (
            "повторите скачивание итогового PDF"
            in response.json()["experience_capture"]["message"]
        )
        assert "Сохранить в базу опыта" not in response.text
        assert "private storage" not in response.text
        assert (await listed(browser))["total"] == 0
        assert (await f.reviews.load(f.job_id)).approved_revision == revision
        monkeypatch.setattr(DocumentCropRenderer, "render", original)
        retry = await browser.post(
            f"/api/v1/experience/capture/{f.job_id}",
            json={"expected_revision": revision},
        )
        assert retry.status_code == 200 and retry.json()["created"] == 2
        assert (await listed(browser))["total"] == 2


async def test_replaced_original_pdf_cannot_create_examples(catalog_flow, monkeypatch):
    """Тот же document ID с другими bytes не является утверждённым источником."""
    f = catalog_flow
    async with client(f) as browser:
        revision = await prepare(browser, f)
        f.catalog.examples.clear()
        f.catalog.events.clear()
        load = f.artifacts.load_request

        async def tampered(**kwargs):
            request = await load(**kwargs)
            request.pdf_content += b"\nchanged after approval"
            return request

        monkeypatch.setattr(f.artifacts, "load_request", tampered)
        response = await browser.post(
            f"/api/v1/experience/capture/{f.job_id}",
            json={"expected_revision": revision},
        )
        assert response.status_code == 422
        assert (await listed(browser))["total"] == 0


@pytest.mark.parametrize("change", ["revoke_area", "edit_review"])
async def test_change_during_crop_aborts_catalog_transaction(
    catalog_flow, monkeypatch, change
):
    """Реальная HTTP-команда между снимком и crop исключает весь устаревший набор."""
    f = catalog_flow
    async with client(f) as browser:
        revision = await prepare(browser, f)
        f.catalog.examples.clear()
        f.catalog.events.clear()
        render = DocumentCropRenderer.render
        changed = False

        async def racing(self, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                body = (
                    {
                        "action": "revoke_area",
                        "expected_confirmation_revision": 1,
                        "reason": "Область неверна",
                    }
                    if change == "revoke_area"
                    else {
                        "action": "edit",
                        "text": "Новое инженерное замечание",
                        "normative_basis": "СП 1",
                    }
                )
                response = await command(
                    browser, f, revision, finding_id="vlm:1", **body
                )
                assert response.status_code == 200, response.text
            return await render(self, **kwargs)

        monkeypatch.setattr(DocumentCropRenderer, "render", racing)
        response = await browser.post(
            f"/api/v1/experience/capture/{f.job_id}",
            json={"expected_revision": revision},
        )
        assert response.status_code == 409 and changed
        assert not f.catalog.examples and not f.catalog.events


async def test_filtered_export_escapes_csv_formula_without_changing_json(catalog_flow):
    """CSV безопасно открывается в таблице; JSON остаётся точным источником текста."""
    f = catalog_flow
    async with client(f) as browser:
        await prepare(browser, f)
        gold = (await listed(browser, tag="gold"))["items"][0]
        text = "=SUM(1,2) инженерное замечание"
        response = await browser.patch(
            f"/api/v1/experience/{gold['id']}",
            json={"expected_revision": 0, "fields": {"text": text}},
        )
        assert response.status_code == 200
        response = await browser.get(
            "/api/v1/experience/export", params={"tag": "gold"}
        )
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            records = json.loads(archive.read("manifest.json"))["examples"]
            assert len(records) == 1 and records[0]["text"] == text
            rows = list(
                csv.DictReader(
                    io.StringIO(archive.read("examples.csv").decode("utf-8-sig"))
                )
            )
            assert rows[0]["text"] == "'" + text
            assert records[0]["source"]["text"] != text


async def test_modified_catalog_during_export_returns_conflict(
    catalog_flow, monkeypatch
):
    """ZIP не выдаётся, если набор редакций изменился во время чтения PNG."""
    f = catalog_flow
    async with client(f) as browser:
        await prepare(browser, f)
        record = (await listed(browser))["items"][0]
        read, changed = LocalCropStore.read, False

        async def racing(self, crop):
            nonlocal changed
            if not changed:
                changed = True
                response = await browser.patch(
                    f"/api/v1/experience/{record['id']}",
                    json={"expected_revision": 0, "fields": {"active": False}},
                )
                assert response.status_code == 200
            return await read(self, crop)

        monkeypatch.setattr(LocalCropStore, "read", racing)
        response = await browser.get("/api/v1/experience/export")
        assert response.status_code == 409 and changed


async def test_corrupt_png_is_neither_served_nor_exported(catalog_flow):
    """Сломанный asset явно недоступен, а не выдаётся как полноценный crop."""
    f = catalog_flow
    async with client(f) as browser:
        await prepare(browser, f)
        record = (await listed(browser))["items"][0]
        example = (await f.catalog.get(UUID(record["id"]))).example
        f.crops._path(example.crops[0].sha256).write_bytes(b"corrupted")
        assert (
            await browser.get(f"/api/v1/experience/{record['id']}/crops/0")
        ).status_code == 503
        response = await browser.get("/api/v1/experience/export")
        assert response.status_code == 503
        assert str(f.crops.root) not in response.text


@pytest.mark.parametrize("limit", ["max_examples", "max_bytes"])
async def test_export_limits_are_explicit_not_truncated(catalog_flow, limit):
    """Слишком большой набор не выдаётся частично и предлагает уточнить фильтры."""
    f = catalog_flow
    async with client(f) as browser:
        await prepare(browser, f)
        app = f.network.apps["experience"]
        app.state.container = replace(
            app.state.container,
            export_catalog=replace(app.state.container.export_catalog, **{limit: 1}),
        )
        response = await browser.get("/api/v1/experience/export")
        assert response.status_code == 422


@pytest.mark.parametrize("operation", ["crop", "curate", "history"])
async def test_resource_job_is_checked_before_crop_curate_or_history(
    catalog_flow, operation
):
    """Доступ к каталогу не обходит проверку задания серверной записи."""
    f = catalog_flow
    async with client(f) as browser:
        await prepare(browser, f)
        entry = (await listed(browser))["items"][0]
        example_id = UUID(entry["id"])
        example = f.catalog.examples[example_id]
        f.catalog.examples[example_id] = replace(
            example, source=replace(example.source, job_id=uuid4())
        )
        url = f"/api/v1/experience/{example_id}"
        if operation == "curate":
            response = await browser.patch(
                url, json={"expected_revision": 0, "fields": {"active": False}}
            )
        else:
            response = await browser.get(
                url + ("/crops/0" if operation == "crop" else "/history")
            )
        assert response.status_code == 404
        assert f.catalog.examples[example_id].revision == 0
