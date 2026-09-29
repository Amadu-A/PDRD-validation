# services/knowledge-service/src/pdrd_knowledge_service/domain/experience_index.py

"""Идентичность проверенного E: версия каталога, область и выбранная формулировка.

Qdrant хранит ссылки на редакции. Текст для VLM всегда берётся заново из Experience.
Принятое замечание не означает исправление проектного документа.
"""

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

KIND = "human_review_v1"


@dataclass(frozen=True, slots=True)
class ExampleReference:
    """Указывает точную серверную редакцию пригодного примера."""

    example_id: UUID
    example_revision: int
    fingerprint: str

    def as_dict(self) -> dict:
        """Возвращает независимый JSON-контракт закрытого канала."""
        return {
            "example_id": str(self.example_id),
            "example_revision": self.example_revision,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class TrustedExample:
    """Проверенная HTTP-адаптером серверная проекция, не пользовательский payload."""

    reference: ExampleReference
    data: dict

    def point_id(self, *, target: str, crop_index: int) -> str:
        """Стабильный ID обновляется на месте при правке текста или области."""
        return str(
            uuid5(
                NAMESPACE_URL,
                f"pdrd:E:{self.reference.example_id}:{target}:{crop_index}",
            )
        )

    def variants(self) -> tuple[tuple[str, str, int], ...]:
        """Создаёт отдельные векторы original/revised для каждой подтверждённой области."""
        return tuple(
            (text["target"], text["text"], index)
            for text in self.data["texts"]
            for index in range(len(self.data["crops"]))
        )

    def payload(self, *, target: str, crop_index: int, identity: str) -> dict:
        """Вектор содержит происхождение и версии, без нормативных доказательств."""
        return {
            **self.reference.as_dict(),
            "kind": KIND,
            "embedding_identity": identity,
            "target": target,
            "crop_index": crop_index,
            "index_point_id": self.point_id(target=target, crop_index=crop_index),
        }
