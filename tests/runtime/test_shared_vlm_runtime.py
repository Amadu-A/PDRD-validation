# tests/runtime/test_shared_vlm_runtime.py

"""Real runtime smoke and determinism tests shared-vlm integration."""

import asyncio
import hashlib
import json
import os
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.vlm_runtime

RUN_VLM_RUNTIME = (
    os.getenv(
        "PDRD_RUN_VLM_RUNTIME_TESTS",
        "",
    )
    == "1"
)

ANALYSIS_URL = "http://pdrd-analysis-service:8501"

SHARED_VLM_URL = "http://shared-vlm:8000"

TEST_IMAGE_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAYElEQVR4nO3PQQ0AIBDAMMC/"
    "50MEj4ZkVbDtmVk/OzrgVQNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNa"
    "A1oDWgNaA1oDWgNaA1oDWgNaA1oDWgPaBXKqA31N0fbGAAAAAElFTkSuQmCC"
)

_TARGET_PAGE_NUMBER = 777

_TARGET_TEXT = (
    "Проверочный инженерный лист. "
    "Насос Н1 — 3 шт. "
    "Кран К1 — 4 шт. "
    "Трубопровод Т1 — Ду50. "
    "В основной надписи указано: Лист 5. "
    "Это тест объективного понимания листа, "
    "ошибки искать не требуется."
)

_SEQUENTIAL_DETERMINISM_RUNS = 5

_PRESSURE_ROUNDS = 3

_PRESSURE_BATCH_SIZE = 8


async def _understand(
    client: httpx.AsyncClient,
    *,
    page_number: int,
    extracted_text: str,
) -> httpx.Response:
    """Вызывает production page-understanding endpoint."""
    return await client.post(
        (f"{ANALYSIS_URL}/internal/v1/pages/understand"),
        json={
            "page_number": page_number,
            "heuristic_page_type": "other",
            "extracted_text": extracted_text,
            "image_base64": TEST_IMAGE_BASE64,
        },
    )


def _facts_payload(
    response: httpx.Response,
) -> dict[str, Any]:
    """Проверяет response и возвращает normalized facts payload."""
    assert response.status_code == 200, response.text

    payload = response.json()

    assert "facts" in payload
    assert "metrics" in payload

    facts = payload["facts"]

    assert isinstance(
        facts,
        dict,
    )

    return facts


def _facts_digest(
    response: httpx.Response,
) -> str:
    """Строит stable SHA256 по semantic facts без runtime metrics."""
    canonical = json.dumps(
        _facts_payload(
            response,
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
    ).encode(
        "utf-8",
    )

    return hashlib.sha256(
        canonical,
    ).hexdigest()


def _noise_text(
    *,
    round_index: int,
    request_index: int,
) -> str:
    """Создаёт соседний request другой длины для изменения dynamic batch."""
    repeated_context = " Дополнительный инженерный контекст." * (request_index + 1)

    return (
        "Параллельный проверочный запрос. "
        f"Раунд {round_index}. "
        f"Запрос {request_index}. "
        "На листе указаны насос, трубопровод, "
        "запорная арматура и шкаф управления."
        f"{repeated_context}"
    )


@pytest.mark.skipif(
    not RUN_VLM_RUNTIME,
    reason=("Requires PDRD_RUN_VLM_RUNTIME_TESTS=1."),
)
async def test_shared_vlm_contract_and_parallel_analysis_requests() -> None:
    """Analysis Service использует resident shared-vlm."""
    timeout = httpx.Timeout(
        600.0,
        connect=30.0,
    )

    async with httpx.AsyncClient(
        timeout=timeout,
    ) as client:
        health_response = await client.get(
            (f"{SHARED_VLM_URL}/health"),
        )

        assert health_response.status_code == 200, health_response.text

        models_response = await client.get(
            (f"{SHARED_VLM_URL}/v1/models"),
        )

        assert models_response.status_code == 200, models_response.text

        models_payload = models_response.json()

        assert any(
            (
                isinstance(
                    item,
                    dict,
                )
                and item.get(
                    "id",
                )
                == "shared-vlm"
            )
            for item in (
                models_payload.get(
                    "data",
                    [],
                )
            )
        )

        responses = await asyncio.gather(
            *(
                _understand(
                    client,
                    page_number=page_number,
                    extracted_text=(
                        "Проверочный инженерный "
                        f"документ, лист {page_number}. "
                        "На странице указан "
                        "шкаф управления."
                    ),
                )
                for page_number in range(
                    1,
                    5,
                )
            )
        )

        for response in responses:
            _facts_payload(
                response,
            )


@pytest.mark.skipif(
    not RUN_VLM_RUNTIME,
    reason=("Requires PDRD_RUN_VLM_RUNTIME_TESTS=1."),
)
async def test_shared_vlm_same_request_is_batch_invariant() -> None:
    """Один request даёт один facts JSON независимо от dynamic batching."""
    timeout = httpx.Timeout(
        600.0,
        connect=30.0,
    )

    async with httpx.AsyncClient(
        timeout=timeout,
    ) as client:
        sequential_digests: list[str] = []

        for _ in range(_SEQUENTIAL_DETERMINISM_RUNS):
            response = await _understand(
                client,
                page_number=(_TARGET_PAGE_NUMBER),
                extracted_text=_TARGET_TEXT,
            )

            sequential_digests.append(
                _facts_digest(
                    response,
                )
            )

        assert (
            len(
                set(
                    sequential_digests,
                )
            )
            == 1
        ), f"Одинаковый VLM request дал разные sequential facts: {sequential_digests}"

        reference_digest = sequential_digests[0]

        pressure_digests: list[str] = []

        for round_index in range(_PRESSURE_ROUNDS):
            requests = [
                _understand(
                    client,
                    page_number=(_TARGET_PAGE_NUMBER),
                    extracted_text=_TARGET_TEXT,
                )
            ]

            requests.extend(
                _understand(
                    client,
                    page_number=(900 + round_index * 100 + request_index),
                    extracted_text=(
                        _noise_text(
                            round_index=(round_index),
                            request_index=(request_index),
                        )
                    ),
                )
                for request_index in range(_PRESSURE_BATCH_SIZE - 1)
            )

            responses = await asyncio.gather(
                *requests,
            )

            target_response = responses[0]

            target_digest = _facts_digest(
                target_response,
            )

            pressure_digests.append(
                target_digest,
            )

            for response in responses[1:]:
                _facts_payload(
                    response,
                )

        assert set(
            pressure_digests,
        ) == {
            reference_digest,
        }, (
            "Одинаковый target request "
            "изменился под concurrent batch pressure. "
            f"reference={reference_digest}; "
            f"pressure={pressure_digests}"
        )
