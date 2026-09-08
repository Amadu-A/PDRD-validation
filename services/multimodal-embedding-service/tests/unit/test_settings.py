# services/multimodal-embedding-service/tests/unit/test_settings.py

"""Unit tests multimodal service safety settings."""

from pdrd_multimodal_embedding_service.settings import (
    ModelSettings,
    Settings,
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

    assert settings.min_free_ram_gib == 20.0

    assert settings.min_free_vram_gib == 18.0

    assert settings.idle_release_seconds == 60.0


def test_memory_thresholds_are_exposed_in_bytes() -> None:
    """GiB thresholds переводятся без decimal/binary путаницы."""
    settings = ModelSettings(
        min_free_ram_gib=20,
        min_free_vram_gib=18,
    )

    assert settings.min_free_ram_bytes == 20 * 1024**3

    assert settings.min_free_vram_bytes == 18 * 1024**3


def test_shared_embedding_identity_can_be_overridden_by_field_name() -> None:
    """Constructor override работает вместе с canonical env aliases."""
    settings = Settings(
        embedding_model="Qwen/Test-Embedding",
        embedding_dimension=3072,
    )

    assert settings.embedding_model == "Qwen/Test-Embedding"

    assert settings.embedding_dimension == 3072

    assert settings.model.name == "Qwen/Test-Embedding"

    assert settings.model.output_dimension == 3072
