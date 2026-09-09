# services/knowledge-service/tests/unit/test_multimodal_settings.py

"""Unit tests safety settings unified embedding retrieval."""

from pdrd_knowledge_service.core.settings import (
    MultimodalEmbeddingSettings,
    QdrantSettings,
    TechnicalAssignmentQueueSettings,
    TechnicalAssignmentSettings,
)


def test_multimodal_embedding_defaults_are_memory_bounded() -> None:
    """8B provider стартует с conservative limits."""
    settings = MultimodalEmbeddingSettings()

    assert settings.model == "Qwen/Qwen3-VL-Embedding-8B"

    assert settings.output_dimension == 4096

    assert settings.model_context_tokens == 32768

    assert settings.max_input_tokens == 8192

    assert settings.max_image_pixels == 1_843_200

    assert settings.max_batch_size == 1

    assert settings.max_concurrency == 1


def test_technical_assignment_queue_uses_single_prefetch() -> None:
    """Worker не забирает несколько тяжёлых GPU jobs заранее."""
    settings = TechnicalAssignmentQueueSettings()

    assert settings.prefetch_count == 1

    assert settings.routing_key == "technical_assignment.index"


def test_unified_embedding_spaces_use_separate_stable_aliases() -> None:
    """N/U, T и E не смешиваются при общей embedding model."""
    qdrant = QdrantSettings()

    technical_assignment = TechnicalAssignmentSettings()

    assert qdrant.normative_collection == "dva_catalog_active"

    assert qdrant.multimodal_collection == "dva_technical_assignment_active"

    assert qdrant.experience_collection == "dva_experience_active"

    assert qdrant.multimodal_collection != qdrant.normative_collection

    assert qdrant.experience_collection != qdrant.normative_collection

    assert qdrant.experience_collection != qdrant.multimodal_collection

    assert qdrant.multimodal_collection != "dva_multimodal_qwen3vl2b_v1"

    assert qdrant.multimodal_collection != "dva_multimodal_qwen3vl8b_v1"

    assert technical_assignment.max_pages == 300

    assert technical_assignment.render_dpi == 150
