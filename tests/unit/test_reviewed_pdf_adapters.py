# tests/unit/test_reviewed_pdf_adapters.py

"""Повреждение кеша и строгий контракт утверждённого PDF на межсервисной границе."""

from copy import deepcopy
from uuid import UUID

import pytest
from pdrd_api_gateway.application.ports.review import ReviewContext, ReviewRequestError
from pdrd_api_gateway.infrastructure.reviewed_pdf_source import HttpReviewedPdfSource
from pdrd_api_gateway.infrastructure.storage.reviewed_pdf_cache import (
    LocalReviewedPdfCache,
)

JOB, DOC = UUID(int=1), UUID(int=2)
BOX = {"x_min": 10.25, "y_min": 20, "x_max": 100, "y_max": 100}
MANIFEST = {
    "job_id": str(JOB),
    "document_id": str(DOC),
    "source_filename": "План.pdf",
    "source_sha256": "a" * 64,
    "digest": "b" * 64,
    "revision": 3,
    "findings": [
        {
            "finding_id": "vlm:1",
            "number": 1,
            "page_number": 1,
            "origin": "vlm",
            "experience_tag": "wise",
            "text": "Полный текст",
            "normative_basis": "",
            "regions": [BOX],
            "callout_box": None,
        }
    ],
}


class Service:
    """Возвращает внешний JSON, который нельзя считать валидным без проверки."""

    def __init__(self, payload):
        """Сохраняет независимый тестовый ответ."""
        self.payload = payload

    async def execute(self, *, context):
        """Не импортирует runtime Experience Service."""
        return self.payload


async def test_cache_is_atomic_bounded_and_checks_content_integrity(tmp_path):
    """Неверный ключ или усечённый PDF перестраивается; на job остаётся один кеш."""
    cache = LocalReviewedPdfCache(tmp_path)
    assert await cache.load(job_id=JOB, key="a" * 64) is None
    await cache.save(job_id=JOB, key="a" * 64, content=b"%PDF-first")
    assert await cache.load(job_id=JOB, key="a" * 64) == b"%PDF-first"
    assert await cache.load(job_id=JOB, key="b" * 64) is None
    await cache.save(job_id=JOB, key="b" * 64, content=b"%PDF-second")
    assert await cache.load(job_id=JOB, key="a" * 64) is None
    assert len(list((tmp_path / str(JOB)).iterdir())) == 1
    artifact = tmp_path / str(JOB) / "reviewed-current.cache"
    artifact.write_bytes(artifact.read_bytes()[:-2])
    assert await cache.load(job_id=JOB, key="b" * 64) is None


async def test_internal_contract_preserves_fractional_regions():
    """Дробные координаты не округляются между Experience и Document Service."""
    manifest = await HttpReviewedPdfSource(Service(MANIFEST)).load(
        context=ReviewContext("engineer:1", JOB, "export")
    )
    assert manifest.document_id == DOC
    assert manifest.findings[0].regions[0].x_min == 10.25


@pytest.mark.parametrize(
    "case", ["job", "page", "tag", "manual", "nan", "bool", "duplicate", "number"]
)
async def test_invalid_internal_projection_does_not_reach_renderer(case):
    """Подмена job, тега, типов или геометрии даёт безопасную ошибку 503."""
    payload = deepcopy(MANIFEST)
    row = payload["findings"][0]
    if case == "job":
        payload["job_id"] = str(UUID(int=99))
    elif case == "page":
        row["page_number"] = 0
    elif case == "tag":
        row["experience_tag"] = "bad"
    elif case == "manual":
        row["origin"] = "manual"
    elif case == "nan":
        row["regions"][0]["x_min"] = float("nan")
    elif case == "bool":
        row["regions"][0]["x_min"] = True
    elif case == "duplicate":
        payload["findings"].append({**row, "number": 2})
    elif case == "number":
        row["number"] = 2
    with pytest.raises(ReviewRequestError) as raised:
        await HttpReviewedPdfSource(Service(payload)).load(
            context=ReviewContext("engineer:1", JOB, "export")
        )
    assert raised.value.status_code == 503
