# services/analysis-service/tests/integration/test_packed_context_stage_http.py

"""Пакет из 31 страницы с N/T/U/D/ПЗ и временным отказом провайдера через настоящий HTTP."""

from collections import Counter
from copy import deepcopy
from uuid import uuid4

import httpx
from pdrd_analysis_service.application.ports.vision_model import VisionModelError
from pdrd_analysis_service.domain.analysis import GenerationResult
from pdrd_analysis_service.transport.http.stage_batch_schemas import (
    CheckNormsStageRequest,
)

from ..unit.test_analysis_use_cases import metrics
from .test_http_api import facts_payload
from .test_stage_batch_concurrency_http import (
    RecordingProgressProbe,
    _build_app,
    _image,
)


class TemporaryFailureModel:
    """Подменяет только GPU-порт и не возвращает заранее подготовленные замечания."""

    def __init__(self):
        """Считает страницы и сохраняет предъявленный контекст."""
        self.calls, self.prompts = Counter(), {}

    async def generate_json(self, **request):
        """Один раз возвращает реальную ошибку порта на четвёртой странице."""
        page = int(request["stage"].split(":")[1])
        self.calls[page] += 1
        self.prompts[page] = request["prompt"]
        if page == 4 and self.calls[page] == 1:
            raise VisionModelError("shared-vlm HTTP 503", status_code=503)
        return GenerationResult({"summary": "Проверено", "violations": []}, metrics())

    async def is_ready(self):
        """Проверка готовности не занимает модель."""
        return True


async def test_packed_context_31_pages_retries_only_failure_and_keeps_all_sources():
    """Транспорт восстанавливает тексты до программы и оставляет все страницы в ответе."""
    quote = "Учебная цитата с условиями и исключениями. " * 10
    pz = "Контекст пояснительной записки. " * 250 + "КОНЕЦ_ПЗ"
    original = {
        "document_id": str(uuid4()),
        "items": [
            {
                "page_number": page,
                "extracted_text": f"Исходная страница {page}.",
                "page_facts": facts_payload(),
                "image_base64": _image(),
                "normative_sources": [
                    {"source_id": "N1", "score": 0.8, "page": 7, "text": quote}
                ],
                "technical_assignment_sources": [
                    {"source_id": "T1", "score": 0.8, "page": 2, "text": quote}
                ],
                "user_package_sources": [
                    {"source_id": "U1", "score": 0.8, "page": 3, "text": quote}
                ],
                "document_context_sources": [
                    {
                        "source_id": "D-p0018-c0",
                        "chunk_index": 0,
                        "page": 18,
                        "text": quote,
                        "evidence_text": quote,
                    }
                ],
                "project_context_sources": [
                    {"source_id": "PZ1", "page": 16, "score": 0.8, "text": pz}
                ],
            }
            for page in range(1, 32)
        ],
    }
    wire = deepcopy(original)
    references = []
    for index, item in enumerate(wire["items"]):
        for name in (
            "normative_sources",
            "technical_assignment_sources",
            "user_package_sources",
            "document_context_sources",
            "project_context_sources",
        ):
            item[name][0]["text"] = None
            references.append(
                {
                    "path": ["items", index, name, 0, "text"],
                    "index": int(name == "project_context_sources"),
                }
            )
    wire.update(
        context_encoding="text-pool-v1",
        context_pool=[quote, pz],
        context_refs=references,
    )
    assert (
        CheckNormsStageRequest.model_validate(wire).model_dump()
        == CheckNormsStageRequest.model_validate(original).model_dump()
    )
    model = TemporaryFailureModel()
    app = _build_app(
        model=model, progress_probe=RecordingProgressProbe(), concurrency=3
    )
    app.state.container.settings.pipeline.vlm_stage_retry_delay_seconds = 0
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/internal/v1/stages/check-norms", json=wire)
    assert response.status_code == 200, response.text
    result = response.json()
    assert [item["page_number"] for item in result["items"]] == list(range(1, 32))
    assert model.calls[4] == 2 and all(
        count == 1 for page, count in model.calls.items() if page != 4
    )
    retry = result["items"][3]["result"]["metrics"]["stage_retry"]
    assert retry["attempts"] == 2 and retry["failed_attempts"][0]["status"] == 503
    for prompt in model.prompts.values():
        for source in ("N1", "T1", "U1", "D-p0018-c0", "PZ1", "КОНЕЦ_ПЗ"):
            assert source in prompt
        assert '"page":16' in prompt
