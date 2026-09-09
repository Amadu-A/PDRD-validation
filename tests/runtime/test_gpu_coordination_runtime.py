# tests/runtime/test_gpu_coordination_runtime.py

"""Real GPU runtime tests cross-container model coordination."""

import asyncio
import os

import httpx
import pytest

pytestmark = pytest.mark.gpu_runtime

RUN_GPU_RUNTIME = (
    os.getenv(
        "PDRD_RUN_GPU_RUNTIME_TESTS",
        "",
    )
    == "1"
)

ANALYSIS_URL = "http://pdrd-analysis-service:8501"

EMBEDDING_URL = "http://pdrd-multimodal-embedding-service:8601"

OLLAMA_URL = "http://ollama:11434"

TEST_IMAGE_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAYElEQVR4nO3PQQ0AIBDAMMC/"
    "50MEj4ZkVbDtmVk/OzrgVQNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNaA1oDWgNa"
    "A1oDWgNaA1oDWgNaA1oDWgNaA1oDWgPaBXKqA31N0fbGAAAAAElFTkSuQmCC"
)


@pytest.mark.skipif(
    not RUN_GPU_RUNTIME,
    reason="Requires PDRD_RUN_GPU_RUNTIME_TESTS=1.",
)
async def test_embedding_residency_blocks_vlm_until_release() -> None:
    """Две PDRD GPU models физически не живут одновременно."""
    timeout = httpx.Timeout(
        600.0,
        connect=30.0,
    )

    async with httpx.AsyncClient(
        timeout=timeout,
    ) as client:
        release_before = await client.post(
            f"{EMBEDDING_URL}/internal/v1/release",
        )

        assert release_before.status_code == 200, release_before.text

        embedding_response = await client.post(
            f"{EMBEDDING_URL}/internal/v1/embeddings",
            json={
                "inputs": [
                    {
                        "text": "Runtime GPU lease contention test.",
                        "instruction": None,
                    }
                ]
            },
        )

        assert embedding_response.status_code == 200, embedding_response.text

        embedding_payload = embedding_response.json()

        assert embedding_payload["dimension"] == 4096

        assert (
            len(
                embedding_payload["embeddings"][0],
            )
            == 4096
        )

        status = (
            await client.get(
                f"{EMBEDDING_URL}/internal/v1/status",
            )
        ).json()

        assert status["model_loaded"] is True

        analysis_task = asyncio.create_task(
            client.post(
                f"{ANALYSIS_URL}/internal/v1/pages/understand",
                json={
                    "page_number": 1,
                    "heuristic_page_type": "other",
                    "extracted_text": (
                        "Проверочный инженерный документ. "
                        "На странице указан шкаф управления."
                    ),
                    "image_base64": TEST_IMAGE_BASE64,
                },
            )
        )

        await asyncio.sleep(
            2,
        )

        # Пока embedding checkpoint удерживает global lease,
        # Analysis должен ждать, а не получить 422/503/OOM.
        assert not analysis_task.done()

        release_response = await client.post(
            f"{EMBEDDING_URL}/internal/v1/release",
        )

        assert release_response.status_code == 200, release_response.text

        analysis_response = await asyncio.wait_for(
            analysis_task,
            timeout=300,
        )

        assert analysis_response.status_code == 200, analysis_response.text

        final_embedding_status = (
            await client.get(
                f"{EMBEDDING_URL}/internal/v1/status",
            )
        ).json()

        assert final_embedding_status["model_loaded"] is False

        ollama_status = (
            await client.get(
                f"{OLLAMA_URL}/api/ps",
            )
        ).json()

        models = ollama_status.get(
            "models",
            [],
        )

        assert isinstance(
            models,
            list,
        )

        assert all(
            str(
                item.get(
                    "name",
                    "",
                )
            )
            != "qwen3-vl:8b-instruct"
            for item in models
            if isinstance(
                item,
                dict,
            )
        )
