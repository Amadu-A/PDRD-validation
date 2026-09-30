# tests/functional/test_experience_quality_api.py

"""HTTP-цикл допуска: независимый отчёт, ручное применение, отзыв и устаревший состав."""

from dataclasses import replace
from uuid import UUID, uuid4

import httpx
import pytest

from tests.artifact_quality_support import report_for
from tests.functional.test_experience_catalog_api import catalog_flow as catalog_flow
from tests.functional.test_experience_catalog_api import listed
from tests.functional.test_experience_index_api import INDEX_KEY
from tests.functional.test_experience_index_api import index_flow as index_flow
from tests.functional.test_experience_versions_api import provide_normative_section
from tests.functional.test_experience_versions_api import version_flow as version_flow
from tests.functional.test_review_api import client
from tests.functional.test_review_api import flow as flow
from tests.functional.test_reviewed_pdf_api import pdf_flow as pdf_flow
from tests.functional.test_reviewed_pdf_api import prepare


async def ready_version(browser, flow):
    """Сборка тестового индекса не присваивает качества автоматически."""
    provide_normative_section(flow, uuid4())
    await prepare(browser, flow)
    gold = (await listed(browser, tag="gold"))["items"][0]
    created = await browser.post(
        "/api/v1/experience-versions",
        json={
            "kind": "vector",
            "name": "Проверочная версия",
            "model": "shared-embedding",
            "items": [{"id": gold["id"], "revision": gold["revision"]}],
        },
    )
    assert created.status_code == 200, created.text
    version = created.json()
    job = await flow.artifacts.claim(
        worker="test", model="shared-embedding", identity="a" * 16, dimension=2
    )
    assert str(job.id) == version["id"]
    await flow.artifacts.finish(
        version_id=job.id, worker="test", result={"status": "ready"}
    )
    return (await browser.get(f"/api/v1/experience-versions/{job.id}")).json()


async def applied(flow, section):
    """Knowledge читает служебным ключом, без прав изменения каталога."""
    async with httpx.AsyncClient(
        transport=flow.network, base_url="http://experience"
    ) as service:
        response = await service.get(
            "/internal/v1/experience-index/applied",
            params={"section_id": section},
            headers={"Authorization": f"Bearer {INDEX_KEY}"},
        )
    assert response.status_code == 200, response.text
    return response.json()["item"]


async def test_quality_approval_apply_revoke_and_restore_are_separate_cas_actions(
    version_flow,
):
    """Отчёт не переключает поиск; отзыв снимает назначение и сохраняет состав."""
    flow = version_flow
    async with client(flow) as browser:
        version = await ready_version(browser, flow)
        url = f"/api/v1/experience-versions/{version['id']}"
        assert await applied(flow, version["section_id"]) is None
        report = report_for(version)
        registered = await browser.post(
            url + "/quality",
            json={"expected_revision": version["revision"], "report": report},
        )
        assert registered.status_code == 200, registered.text
        approved = registered.json()
        assert approved["quality_approved"] and approved["quality_report"] == report
        assert await applied(flow, version["section_id"]) is None
        assert (
            await browser.post(
                url + "/apply", json={"expected_revision": version["revision"]}
            )
        ).status_code == 409
        assert (
            await browser.post(
                url + "/apply", json={"expected_revision": approved["revision"]}
            )
        ).status_code == 200
        active = await applied(flow, version["section_id"])
        assert active["id"] == version["id"]
        assert await applied(flow, "Другой раздел") is None
        stale = await browser.request(
            "DELETE", url + "/quality", json={"expected_revision": approved["revision"]}
        )
        assert stale.status_code == 409
        revoked = await browser.request(
            "DELETE", url + "/quality", json={"expected_revision": active["revision"]}
        )
        assert revoked.status_code == 200, revoked.text
        assert (
            not revoked.json()["quality_approved"]
            and revoked.json()["quality_report"] is None
        )
        assert revoked.json()["members"] == version["members"]
        assert await applied(flow, version["section_id"]) is None
        assert (
            await browser.post(
                url + "/apply", json={"expected_revision": revoked.json()["revision"]}
            )
        ).status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        "version_id",
        "manifest_sha256",
        "collection",
        "model",
        "section_id",
        "dimension",
        "leakage",
        "duplicates",
        "fake_metrics",
        "nan",
        "future",
        "schema",
        "oversize",
    ],
)
async def test_invalid_quality_report_cannot_create_admission(version_flow, change):
    """Подмена привязки, слабая оценка и утечка исходного PDF отклоняются."""
    flow = version_flow
    async with client(flow) as browser:
        version = await ready_version(browser, flow)
        report = report_for(version)
        if change in {
            "version_id",
            "manifest_sha256",
            "collection",
            "model",
            "section_id",
        }:
            report[change] = "подмена"
        elif change == "dimension":
            report[change] += 1
        elif change == "leakage":
            report["evaluation_document_sha256"][0] = version["members"][0][
                "source_sha256"
            ]
        elif change == "duplicates":
            report["evaluation_document_sha256"][0] = report[
                "evaluation_document_sha256"
            ][1]
        elif change == "fake_metrics":
            report["with_experience"]["precision"] = 0.1
        elif change == "nan":
            report["recall_at_k"] = "NaN"
        elif change == "future":
            report["evaluated_at"] = "2099-01-01T00:00:00+00:00"
        elif change == "schema":
            report["report_schema_version"] = True
        else:
            report["padding"] = "a" * 70000
        response = await browser.post(
            f"/api/v1/experience-versions/{version['id']}/quality",
            json={"expected_revision": version["revision"], "report": report},
        )
        assert response.status_code == 422, response.text
        assert (await flow.artifacts.get(UUID(version["id"]))).revision == version[
            "revision"
        ]
        assert await applied(flow, version["section_id"]) is None


async def test_failed_report_is_recorded_without_admission_and_stale_members_stop_search(
    version_flow,
):
    """Отрицательная оценка сохраняется; curation принятого состава закрывает E."""
    flow = version_flow
    async with client(flow) as browser:
        version = await ready_version(browser, flow)
        url = f"/api/v1/experience-versions/{version['id']}"
        report = report_for(version)
        report.update(approved=False, retrieval_cases=1)
        failed = await browser.post(
            url + "/quality",
            json={"expected_revision": version["revision"], "report": report},
        )
        assert failed.status_code == 200 and not failed.json()["quality_approved"]
        good = report_for(version)
        approved = await browser.post(
            url + "/quality",
            json={"expected_revision": failed.json()["revision"], "report": good},
        )
        assert approved.status_code == 200, approved.text
        assert (
            await browser.post(
                url + "/apply", json={"expected_revision": approved.json()["revision"]}
            )
        ).status_code == 200
        assert await applied(flow, version["section_id"])
        example_id = UUID(version["members"][0]["example_id"])
        flow.catalog.examples[example_id] = replace(
            flow.catalog.examples[example_id], revision=100
        )
        assert await applied(flow, version["section_id"]) is None


async def test_index_worker_cannot_approve_and_prepared_dataset_is_not_trained_weights(
    version_flow,
):
    """Служебный индексный ключ не вправе менять качество или назначать GPU-модель."""
    flow = version_flow
    async with client(flow) as browser:
        version = await ready_version(browser, flow)
    async with httpx.AsyncClient(
        transport=flow.network, base_url="http://experience"
    ) as service:
        response = await service.post(
            f"/internal/v1/experience-versions/{version['id']}/quality",
            json={
                "expected_revision": version["revision"],
                "report": report_for(version),
            },
            headers={
                "Authorization": f"Bearer {INDEX_KEY}",
                "X-Review-Actor": "engineer:test",
            },
        )
        assert response.status_code == 403
    item = flow.artifacts.rows[UUID(version["id"])]
    dataset = replace(item, kind="fine_tune", status="prepared")
    flow.artifacts.rows[item.id] = dataset
    async with client(flow) as browser:
        response = await browser.post(
            f"/api/v1/experience-versions/{version['id']}/quality",
            json={"expected_revision": dataset.revision, "report": report_for(dataset)},
        )
        assert response.status_code == 422
