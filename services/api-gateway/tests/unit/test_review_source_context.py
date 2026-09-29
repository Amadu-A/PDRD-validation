# services/api-gateway/tests/unit/test_review_source_context.py

"""Нормативный раздел Human Review берётся из серверного задания и базы Knowledge."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pdrd_api_gateway.application.ports.normative_catalog import (
    NormativeCatalogReadError,
    NormativeSectionRecord,
)
from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.application.use_cases.get_review_source import GetReviewSource
from pdrd_api_gateway.core.settings import KnowledgeServiceSettings
from pdrd_api_gateway.domain.analysis_job import AnalysisJobStatus
from pdrd_api_gateway.infrastructure.knowledge.normative_catalog import (
    HttpNormativeCatalogReader,
)


def source_context(mode):
    """Артефакты имеют собственный fallback; snapshot задания имеет приоритет."""
    job_id, document_id, section_id = uuid4(), uuid4(), uuid4()
    snapshot = SimpleNamespace(section_id=section_id)
    job = SimpleNamespace(
        status=AnalysisJobStatus.COMPLETED,
        document_id=document_id,
        normative_snapshot=snapshot if mode in {"job", "preferred"} else None,
    )
    artifact = SimpleNamespace(
        submission=SimpleNamespace(document_id=document_id, pdf_file_name="План.pdf"),
        pdf_content=b"%PDF-1.7\noriginal",
        normative_snapshot=snapshot
        if mode == "artifacts"
        else SimpleNamespace(section_id=uuid4())
        if mode == "preferred"
        else None,
    )
    reader = SimpleNamespace(
        get_section=AsyncMock(
            return_value=NormativeSectionRecord(
                section_id, "Инструкция", "Охранная сигнализация"
            )
        )
    )
    use_case = GetReviewSource(
        SimpleNamespace(execute=AsyncMock(return_value=job)),
        SimpleNamespace(
            load_request=AsyncMock(return_value=artifact),
            load_result=AsyncMock(return_value={}),
        ),
        SimpleNamespace(execute=AsyncMock(return_value={"pages": []})),
        reader,
    )
    return job_id, section_id, reader, use_case


@pytest.mark.parametrize("mode", ["job", "artifacts", "preferred", "legacy"])
async def test_review_section_uses_saved_snapshot_and_catalog_name(mode):
    """Исторический анализ без snapshot не получает выдуманный раздел."""
    job_id, section_id, reader, use_case = source_context(mode)
    source = await use_case.execute(job_id=job_id)
    if mode == "legacy":
        assert source.section_id == source.section_title == ""
        reader.get_section.assert_not_awaited()
    else:
        assert source.section_id == str(section_id)
        assert source.section_title == "Охранная сигнализация"
        reader.get_section.assert_awaited_once_with(section_id=section_id)


async def test_wrong_section_response_cannot_replace_authoritative_scope():
    """Даже серверный ответ другого раздела отклоняется до сохранения каталога."""
    job_id, _, reader, use_case = source_context("job")
    reader.get_section.return_value = NormativeSectionRecord(
        uuid4(), "Инструкция", "Другой раздел"
    )
    with pytest.raises(ReviewRequestError) as failure:
        await use_case.execute(job_id=job_id)
    assert failure.value.status_code == 502


async def test_unavailable_normative_context_reports_retry():
    """Сбой базы нормативки не выдаёт предположение за достоверный раздел."""
    job_id, _, reader, use_case = source_context("job")
    reader.get_section.side_effect = NormativeCatalogReadError("Недоступен")
    with pytest.raises(ReviewRequestError) as failure:
        await use_case.execute(job_id=job_id)
    assert failure.value.status_code == 503


@pytest.mark.parametrize("name", ["Охранная сигнализация", 123])
async def test_normative_reader_preserves_section_name_or_rejects_invalid_type(
    name, monkeypatch
):
    """Название проходит через существующий HTTP-адаптер, неправильный тип не превращается в пустой раздел."""
    section_id = uuid4()
    reader = HttpNormativeCatalogReader(
        settings=KnowledgeServiceSettings(base_url="http://knowledge")
    )
    call = AsyncMock(
        return_value={
            "section_id": str(section_id),
            "name": name,
            "system_prompt": "Инструкция",
        }
    )
    monkeypatch.setattr(reader, "_get_json", call)
    if isinstance(name, str):
        assert (await reader.get_section(section_id=section_id)).name == name
    else:
        with pytest.raises(NormativeCatalogReadError):
            await reader.get_section(section_id=section_id)
    call.assert_awaited_once_with(path=f"/internal/v1/normative/sections/{section_id}")
