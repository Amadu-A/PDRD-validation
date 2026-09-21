# tests/runtime/test_shared_vlm_runtime.py

"""Real runtime smoke tests shared-vlm integration."""

import asyncio
import os

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


@pytest.mark.skipif(
    not RUN_VLM_RUNTIME,
    reason=("Requires PDRD_RUN_VLM_RUNTIME_TESTS=1."),
)
async def test_shared_vlm_contract_and_parallel_analysis_requests() -> None:
    """Analysis Service использует resident shared-vlm без project GPU lease."""
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

        async def understand(
            page_number: int,
        ) -> httpx.Response:
            return await client.post(
                (f"{ANALYSIS_URL}/internal/v1/pages/understand"),
                json={
                    "page_number": (page_number),
                    "heuristic_page_type": ("other"),
                    "extracted_text": (
                        "Проверочный инженерный "
                        f"документ, лист {page_number}. "
                        "На странице указан "
                        "шкаф управления."
                    ),
                    "image_base64": (TEST_IMAGE_BASE64),
                },
            )

        responses = await asyncio.gather(
            *(
                understand(
                    page_number,
                )
                for page_number in range(
                    1,
                    5,
                )
            )
        )

        for response in responses:
            assert response.status_code == 200, response.text

            payload = response.json()

            assert "facts" in payload

            assert "metrics" in payload
