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

TECHNICAL_ASSIGNMENT = KNOWLEDGE_ROOT / "domain" / "technical_assignment.py"


def test_source_semantics_fix_n_t_u_e_contract() -> None:
    """Закрепляет согласованную семантику source IDs."""
    content = SOURCE_SEMANTICS.read_text(
        encoding="utf-8",
    )

    required = (
        'NORMATIVE = "N"',
        'TECHNICAL_ASSIGNMENT = "T"',
        'USER_PACKAGE = "U"',
        'EXPERIENCE = "E"',
    )

    missing = [marker for marker in required if marker not in content]

    assert not missing, "\n".join(
        missing,
    )


def test_text_and_multimodal_embeddings_remain_separate() -> None:
    """Новая VL-модель не заменяет существующий text embedding."""
    content = SETTINGS.read_text(
        encoding="utf-8",
    )

    required = (
        'model: str = "qwen3-embedding:4b"',
        '"Qwen/Qwen3-VL-Embedding-8B"',
        '"dva_normative_v2"',
        '"dva_multimodal_v1"',
    )

    missing = [marker for marker in required if marker not in content]

    assert not missing, "\n".join(
        missing,
    )


def test_multimodal_boundary_accepts_text_and_images() -> None:
    """Application зависит от port, а не от PyTorch/Transformers."""
    content = MULTIMODAL_PORT.read_text(
        encoding="utf-8",
    )

    assert "class MultimodalEmbeddingProvider" in content

    assert "text: str | None" in content

    assert "image_bytes: bytes | None" in content

    forbidden = (
        "import torch",
        "from torch",
        "transformers",
        "sentence_transformers",
        "CUDA",
    )

    assert not any(marker in content for marker in forbidden)


def test_multimodal_runtime_has_conservative_safety_limits() -> None:
    """Закрепляет базовую GPU/backpressure политику."""
    content = SETTINGS.read_text(
        encoding="utf-8",
    )

    required = (
        "default=8192",
        "default=1_843_200",
        "max_batch_size",
        "max_concurrency",
        "prefetch_count",
        '"technical_assignment.index"',
    )

    missing = [marker for marker in required if marker not in content]

    assert not missing, "\n".join(
        missing,
    )

    assert content.count("default=1,") >= 3
