# services/multimodal-embedding-service/tests/unit/test_settings.py

"""Unit tests multimodal service safety settings."""

from pdrd_multimodal_embedding_service.settings import (
    ModelSettings,
)


def test_default_model_is_qwen3_vl_embedding_8b() -> None:
    """Закрепляет выбранный production checkpoint."""
    settings = ModelSettings()

    assert settings.name == "Qwen/Qwen3-VL-Embedding-8B"

    assert settings.output_dimension == 4096

    assert settings.model_context_tokens == 32768


def test_default_runtime_is_memory_bounded() -> None:
    """Heavy runtime стартует с conservative limits."""
    settings = ModelSettings()

    assert settings.max_input_tokens == 8192

    assert settings.max_image_pixels == 1_843_200

    assert settings.max_batch_size == 1

    assert settings.max_concurrency == 1

    assert settings.min_free_vram_gib == 18.0
