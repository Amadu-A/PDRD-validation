# services/analysis-service/tests/integration/test_graphics_without_document_context.py

"""Базовое чтение графики без D через настоящий Understanding, PIL и HTTP-контракт."""

from dataclasses import asdict
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

import pytest
from pdrd_analysis_service.application.prompts import build_normative_check_prompt
from pdrd_analysis_service.application.use_cases.understanding import UnderstandPage
from pdrd_analysis_service.application.vision_execution import current_vision_execution
from pdrd_analysis_service.domain.analysis import GenerationMetrics, GenerationResult
from pdrd_analysis_service.domain.document_context import LocatedDocumentFact
from pdrd_analysis_service.domain.engineering_assertion import (
    assertion_from_facts,
    read_assertion,
)
from pdrd_analysis_service.domain.engineering_facts import explicit_system_purposes
from pdrd_analysis_service.domain.equipment_specification import SpecificationCancelled
from pdrd_analysis_service.infrastructure.image_cropper import PilImageCropper
from pdrd_analysis_service.transport.http.schemas import (
    FindingDraftPayload,
    PageFactsPayload,
)
from pdrd_analysis_service.transport.http.vision_execution import page_execution_context
from PIL import Image
from starlette.requests import Request


def test_assertion_survives_existing_http_contract_without_new_storage():
    """HTTP между этапами сохраняет тип, объект, условия и исходные наблюдения утверждения."""
    fact = explicit_system_purposes("Назначение системы: водоснабжение (В2)")[0]
    assertion = assertion_from_facts((LocatedDocumentFact(8, 1, fact),))
    payload = FindingDraftPayload(
        finding_id="f1",
        page=8,
        page_type="text",
        category="document_consistency",
        severity="warning",
        status="needs_review",
        comment="Разное назначение В2",
        evidence=fact.evidence_text,
        recommendation_draft="",
        confidence=0.8,
        normative_source_ids=[],
        basis="",
        basis_sources=[],
        experience_query="",
        origin_assertions=[assertion.as_origin()],
    )
    decoded = FindingDraftPayload.model_validate_json(
        payload.model_dump_json()
    ).to_domain()
    assert read_assertion(decoded.origin_assertions) == assertion


class GraphicsModel:
    """Выбирает целевой фрагмент связи и возвращает наблюдение вместо готового замечания."""

    def __init__(self):
        """Фиксирует количество и контекст обращений."""
        self.calls = []

    async def generate_json(self, **request):
        """Только второй вызов читает маленький crop, первый не создаёт D-факты."""
        self.calls.append(request)
        if request["stage"].startswith("page_understanding:"):
            assert "document_facts" not in request["schema"]["properties"]
            assert "graphical_targets" in request["schema"]["properties"]
            payload = {
                "discipline": "ВК",
                "page_type": "drawing",
                "summary": "Схема S7",
                "objects": ["S7"],
                "connections": [],
                "labels": [],
                "normative_queries": [],
                "graphical_targets": [
                    {
                        "object_ref": "S7",
                        "kind": "callout",
                        "question": "К какому концу линии относится выноска S7?",
                        "observation": "Выноска S7 у соединения",
                        "regions": [
                            {
                                "x_min": 201,
                                "y_min": 301,
                                "x_max": 499,
                                "y_max": 599,
                                "confidence": 0.9,
                            }
                        ],
                    }
                ],
            }
        else:
            assert "Целевой вопрос чтения" in request["prompt"]
            payload = {
                "readable": True,
                "value_raw": "Выноска S7 относится к правому концу линии",
                "evidence_text": "S7 — правый конец линии",
                "region": {
                    "x_min": 0,
                    "y_min": 0,
                    "x_max": 1000,
                    "y_max": 1000,
                    "confidence": 0.9,
                },
            }
        return GenerationResult(
            payload, GenerationMetrics(1, "stop", 1500, 1, 0, 1, 1, 1, 0)
        )


def image_bytes():
    """Создаёт маленький растр для проверки реального преобразования координат."""
    buffer = BytesIO()
    Image.new("RGB", (101, 203), "white").save(buffer, format="PNG")
    return buffer.getvalue()


async def test_base_graphical_reading_without_d_is_localized_and_forwarded():
    """Чёткая конкретная цель запускает crop без D, а исходные PDF-координаты доходят до Check Norms."""
    model = GraphicsModel()
    facts, metrics = await UnderstandPage(
        model, 1500, image_cropper=PilImageCropper()
    ).execute(
        page_number=7,
        heuristic_page_type="drawing",
        extracted_text="Схема S7",
        image_bytes=image_bytes(),
        use_document_context=False,
    )
    assert (
        len(model.calls) == 2
        and metrics.as_dict()["understanding"]["vlm_call_count"] == 2
    )
    assert (
        len(facts.document_facts) == 1
        and facts.document_facts[0].extraction_method == "vlm_crop"
    )
    region = facts.document_facts[0].visual_regions[0]
    assert region["x_min"] == round(20 / 101 * 1000)
    assert region["y_min"] == round(61 / 203 * 1000)
    assert facts.document_facts[0].source_pages == (7,)
    payload = PageFactsPayload.model_validate(asdict(facts))
    forwarded = payload.to_domain()
    assert forwarded.graphical_targets == facts.graphical_targets
    assert forwarded.document_facts == facts.document_facts
    prompt = build_normative_check_prompt(
        page_number=7,
        extracted_text="Схема S7",
        page_facts=forwarded,
        normative_sources=(),
        normative_text_limit=500,
    )
    assert "S7 — правый конец линии" in prompt and "engineering_facts" in prompt
    assert "DOCUMENT CONTEXT SOURCES:" not in prompt


async def test_cancellation_before_crop_prevents_extra_model_call():
    """Отмена между общим чтением и целевым фрагментом не вызывает дополнительный inference."""

    async def cancelled():
        """Эмулирует сохранённую отмену."""
        return True

    model = GraphicsModel()
    with pytest.raises(SpecificationCancelled):
        await UnderstandPage(model, 1500, image_cropper=PilImageCropper()).execute(
            page_number=7,
            heuristic_page_type="drawing",
            extracted_text="Схема S7",
            image_bytes=image_bytes(),
            use_document_context=False,
            should_stop=cancelled,
        )
    assert len(model.calls) == 1


async def test_localization_correlates_finished_job_without_mutating_progress():
    """Отложенное чтение готового отчёта имеет идентичность задания и не трогает его завершённый прогресс."""
    identifier = UUID("10000000-0000-0000-0000-000000000007")
    request = Request(
        {"type": "http", "path": "/internal/v1/findings/localize", "headers": []}
    )
    # У контейнера намеренно нет progress probe: для завершённого отчёта он не нужен.
    async for _ in page_execution_context(request, SimpleNamespace(), identifier):
        execution = current_vision_execution.get()
        assert execution.job_key == str(identifier) and execution.report_gpu is None
        await execution.checkpoint()
    assert current_vision_execution.get() is None
