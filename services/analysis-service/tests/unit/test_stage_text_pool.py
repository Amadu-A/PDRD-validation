# services/analysis-service/tests/unit/test_stage_text_pool.py

"""Транспортное восстановление длинных повторных текстов без изменения доказательств."""

from copy import deepcopy
from dataclasses import asdict

import pytest
from pdrd_analysis_service.transport.http.stage_batch_schemas import (
    CheckNormsStageRequest,
)

from .test_analysis_use_cases import page_facts


def payload():
    """Минимальный штатный запрос без зависимости от внешнего хранилища."""
    return {
        "document_id": "00000000-0000-4000-8000-000000000001",
        "items": [
            {
                "page_number": 1,
                "extracted_text": "Исходная цитата ПЗ. " * 100,
                "page_facts": asdict(page_facts()),
                "normative_sources": [],
                "image_base64": "eA==",
            }
        ],
    }


def test_text_pool_restores_exact_native_text_and_does_not_mutate_wire_request():
    """В use case попадает исходный текст, служебных ссылок в источниках не остаётся."""
    original = payload()
    packed = deepcopy(original)
    packed.update(
        context_encoding="text-pool-v1",
        context_pool=[original["items"][0]["extracted_text"]],
        context_refs=[{"path": ["items", 0, "extracted_text"], "index": 0}],
    )
    packed["items"][0]["extracted_text"] = None
    snapshot = deepcopy(packed)
    assert (
        CheckNormsStageRequest.model_validate(packed).model_dump()
        == CheckNormsStageRequest.model_validate(original).model_dump()
    )
    assert packed == snapshot


@pytest.mark.parametrize(
    "reference",
    [
        {"path": ["items", 0, "extracted_text"], "index": -1},
        {"path": ["items", 0, "extracted_text"], "index": True},
        {"path": ["items", 4, "extracted_text"], "index": 0},
        {"path": ["document_id"], "index": 0},
    ],
)
def test_invalid_pool_reference_is_rejected_before_inference(reference):
    """Ошибочная ссылка не подменяет текст пустой строкой и не доходит до GPU."""
    packed = payload()
    packed.update(
        context_encoding="text-pool-v1",
        context_pool=["Цитата"],
        context_refs=[reference],
    )
    with pytest.raises(ValueError):
        CheckNormsStageRequest.model_validate(packed)
