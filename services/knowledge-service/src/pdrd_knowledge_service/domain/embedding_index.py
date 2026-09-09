# services/knowledge-service/src/pdrd_knowledge_service/domain/embedding_index.py

"""Immutable identity и physical collection plan embeddings."""

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class EmbeddingIdentity:
    """Определяет совместимость одного vector space."""

    model: str

    dimension: int

    schema_version: int

    @property
    def fingerprint(
        self,
    ) -> str:
        """Возвращает deterministic short fingerprint."""
        source = (
            f"{self.model.strip()}|{self.dimension}|{self.schema_version}"
        ).encode()

        return sha256(
            source,
        ).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class EmbeddingIndexPlan:
    """Stable aliases и model-specific physical collections."""

    identity: EmbeddingIdentity

    catalog_alias: str

    technical_assignment_alias: str

    experience_alias: str

    catalog_prefix: str

    technical_assignment_prefix: str

    experience_prefix: str

    @property
    def catalog_target(
        self,
    ) -> str:
        """Возвращает physical catalog collection."""
        return f"{self.catalog_prefix}_{self.identity.fingerprint}"

    @property
    def technical_assignment_target(
        self,
    ) -> str:
        """Возвращает physical T collection."""
        return f"{self.technical_assignment_prefix}_{self.identity.fingerprint}"

    @property
    def experience_target(
        self,
    ) -> str:
        """Возвращает physical E collection."""
        return f"{self.experience_prefix}_{self.identity.fingerprint}"

    @property
    def aliases_to_targets(
        self,
    ) -> dict[str, str]:
        """Возвращает полный atomic alias cutover map."""
        return {
            self.catalog_alias: self.catalog_target,
            self.technical_assignment_alias: (self.technical_assignment_target),
            self.experience_alias: self.experience_target,
        }
