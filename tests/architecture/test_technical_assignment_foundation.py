# tests/architecture/test_technical_assignment_foundation.py

"""Architecture guards foundation технического задания."""

from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

KNOWLEDGE_ROOT = (
    REPOSITORY_ROOT
    / "services"
    / "knowledge-service"
    / "src"
    / "pdrd_knowledge_service"
)

SETTINGS = KNOWLEDGE_ROOT / "core" / "settings.py"

MULTIMODAL_PORT = KNOWLEDGE_ROOT / "application" / "ports" / "multimodal_embedding.py"

SOURCE_SEMANTICS = KNOWLEDGE_ROOT / "domain" / "source_semantics.py"


def test_source_semantics_fix_n_t_u_e_contract() -> None:
    """Закрепляет source IDs."""
    content = SOURCE_SEMANTICS.read_text(
        encoding="utf-8",
    )

    required = (
        'NORMATIVE = "N"',
        'TECHNICAL_ASSIGNMENT = "T"',
        'USER_PACKAGE = "U"',
        'EXPERIENCE = "E"',
    )

    assert all(marker in content for marker in required)


def test_all_embeddings_use_one_model_identity() -> None:
    """Text и multimodal retrieval больше не используют разные models."""
    content = SETTINGS.read_text(
        encoding="utf-8",
    )

    assert '"Qwen/Qwen3-VL-Embedding-8B"' in content

    assert "qwen3-embedding:4b" not in content

    assert "synchronize_embedding_identity" in content

    assert "embedding_index_plan" in content


def test_multimodal_boundary_accepts_text_and_images() -> None:
    """Application остаётся framework/provider agnostic."""
    content = MULTIMODAL_PORT.read_text(
        encoding="utf-8",
    )

    assert "class MultimodalEmbeddingProvider" in content

    assert "text: str | None" in content

    assert "image_bytes: bytes | None" in content

    forbidden = (
        "import torch",
        "from torch",
        "sentence_transformers",
    )

    assert not any(marker in content for marker in forbidden)


def test_multimodal_runtime_has_conservative_limits() -> None:
    """T worker остаётся bounded."""
    content = SETTINGS.read_text(
        encoding="utf-8",
    )

    required = (
        "max_batch_size",
        "max_concurrency",
        "prefetch_count",
        '"technical_assignment.index"',
    )

    assert all(marker in content for marker in required)
