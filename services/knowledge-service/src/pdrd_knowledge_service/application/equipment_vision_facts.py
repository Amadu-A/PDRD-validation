# services/knowledge-service/src/pdrd_knowledge_service/application/equipment_vision_facts.py

"""Нормализация адресных VLM-фактов без автоматического подтверждения."""

from hashlib import sha256
from typing import Any

from pdrd_knowledge_service.application.equipment_facts import extract_equipment_facts


def normalize_equipment_vision_facts(
    *,
    source_id: str,
    model: str,
    variant: str,
    facts: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    """Применяет тот же парсер единиц и сохраняет необходимость проверки."""
    result: list[dict[str, Any]] = []
    for raw in facts[:16]:
        page = raw.get("page")
        snippet = str(raw.get("snippet") or "").strip()[:300]
        if (
            isinstance(page, bool)
            or not isinstance(page, int)
            or not 1 <= page <= 40
            or not snippet
        ):
            continue
        fields = [
            str(raw.get(key) or "").strip()[:120]
            for key in ("property_name", "value_raw", "unit_raw")
        ]
        if not all(fields[:2]):
            continue
        line = " | ".join((model, variant, " ".join(fields)))
        parsed = extract_equipment_facts(
            source_id=source_id,
            model=model,
            variant=variant,
            pages=[{"page": page, "text": line}],
        )
        for fact in parsed:
            stable = "|".join(
                (
                    source_id,
                    str(page),
                    fact["property_type"],
                    fact["value_raw"],
                    snippet,
                )
            )
            fact["fact_id"] = "EQF-" + sha256(stable.encode()).hexdigest()[:20]
            fact.update(
                confidence=0.6,
                applicability="vision_review",
                snippet=snippet,
                row_label=snippet,
                column_label=model,
                current_type=raw.get("current_type")
                if raw.get("current_type") in {"AC", "DC", "AC/DC"}
                else "",
                phase=raw.get("phase")
                if raw.get("phase") in {"single", "three"}
                else "",
                condition=snippet,
            )
            result.append(fact)
    return result[:16]
