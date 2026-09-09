# services/knowledge-service/src/pdrd_knowledge_service/domain/source_semantics.py

"""Семантика источников доказательств PDRD."""

from enum import StrEnum


class EvidenceSourceKind(StrEnum):
    """Фиксирует префиксы разных типов источников."""

    NORMATIVE = "N"

    TECHNICAL_ASSIGNMENT = "T"

    USER_PACKAGE = "U"

    EXPERIENCE = "E"


def build_source_id(
    *,
    kind: EvidenceSourceKind,
    position: int,
) -> str:
    """Строит стабильный source_id для одного retrieval result."""
    if position < 1:
        raise ValueError(
            "Позиция source_id должна быть положительной.",
        )

    return f"{kind.value}{position}"
