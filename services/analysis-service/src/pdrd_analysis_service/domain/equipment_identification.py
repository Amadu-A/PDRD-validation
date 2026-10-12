# services/analysis-service/src/pdrd_analysis_service/domain/equipment_identification.py

"""Проверяет обозначения по тексту проекта без угадывания производителя."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

_MODEL = re.compile(
    r"(?<![\w-])(?:[A-ZА-ЯЁ]{1,5}\s+\d+(?:[-–—][\w]+)+|"
    r"[A-ZА-ЯЁa-zа-яё][\w]*[-–—]\s*[\w]*\d[\w]*(?:[-–—][\w]+)*|"
    r"[A-ZА-ЯЁ]{2,6}\d[\w]*)(?![\w-])"
)
_EQUIPMENT = re.compile(
    r"^(?:прибор|источник|резервированный\s+источник|извещатель|устройство|"
    r"контроллер|блок|модуль|автомат|контактор|реле|датчик|насос|вентилятор)\b",
    re.I,
)
_NON_EQUIPMENT = re.compile(r"^(?:порт|интерфейс|линия|шлейф|кабель)\b", re.I)


def _quantity_visible(parameter: Mapping[str, Any], text: str) -> bool:
    """Не превращает число в маркировке изделия в проектное напряжение или ток."""
    value = str(parameter.get("value_raw") or "").strip()
    unit = str(parameter.get("unit_raw") or "").strip().casefold()
    if not unit or not re.search(r"\d", value):
        return True
    groups = (
        ("v", "в"),
        ("a", "а"),
        ("w", "вт"),
        ("hz", "гц"),
        ("mv", "мв"),
        ("kv", "кв"),
        ("ma", "ма"),
        ("kw", "квт"),
        ("°c", "°с"),
    )
    aliases = next((group for group in groups if unit in group), (unit,))
    parts = re.split(
        r"\s*(?:\.{2,3}|…|[–—])\s*|(?<=\d)\s*-\s*(?=[+-]?\d)",
        value.casefold().replace(",", "."),
    )
    numeric = r"\s*(?:\.{2,3}|…|[-–—])\s*".join(
        re.escape(part.strip()) for part in parts
    )
    units = "|".join(re.escape(alias) for alias in aliases)
    pattern = rf"(?<![\w\-–—]){numeric}\s*(?:{units})(?:\s*(?:ac|dc))?(?!\w)"
    return re.search(pattern, text.casefold().replace(",", ".")) is not None


def identity_token(value: str) -> str:
    """Сравнивает написания с типографскими дефисами и незначащими пробелами."""
    value = value.casefold().translate(str.maketrans("–—−", "---"))
    value = re.sub(r"\bисп(?:олнение)?\.?\s*", "исп.", value)
    return re.sub(r"\s+", "", value)


def contains_identity(text: str, identifier: str) -> bool:
    """Не принимает соседнюю модель или часть слова за явную маркировку."""
    if not identifier.strip():
        return False
    parts = re.split(r"[\s\-–—−]+", identifier.casefold().strip())
    needle = r"[\s\-–—−]*".join(
        r"\s*".join(re.escape(char) for char in part) for part in parts
    )
    return (
        re.search(rf"(?<![\w\-–—−]){needle}(?![\w\-–—−])", text.casefold()) is not None
    )


def text_equipment_records(text: str) -> list[dict[str, Any]]:
    """Читает явно подписанные строки оборудования, сохраняя дословное доказательство."""
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not _EQUIPMENT.search(line):
            continue
        match = _MODEL.search(line)
        if match is None:
            continue
        model = re.sub(r"\s+", " ", match.group()).strip()
        variant = line[match.end() :].strip().strip("«»\"'").strip()
        variant_kind = (
            "interface"
            if re.fullmatch(r"\d*\s*RS[- ]?485", variant, re.I)
            else "execution"
            if re.match(r"исп(?:олнение)?[.\s]", variant, re.I)
            else "model_name"
            if any(mark in line[match.end() :] for mark in ('"', "«", "»"))
            else "execution"
            if variant
            else "none"
        )
        records.append(
            {
                "manufacturer": "",
                "model": model,
                "variant": variant,
                "variant_kind": variant_kind,
                "article": "",
                "equipment_type": line[: match.start()].strip(),
                "object_ref": "",
                "evidence_text": line,
                "visual_regions": [],
                "parameters": [],
                "confidence": 1.0,
                "status": "needs_review",
                "entity_kind": "equipment",
                "model_status": "confirmed",
                "manufacturer_status": "unknown",
                "manufacturer_evidence_text": "",
                "identification_reason": "manufacturer_unknown",
            }
        )
    return records


def ground_equipment_records(
    records: Sequence[Mapping[str, Any]], text: str, *, limit: int = 2000
) -> tuple[dict[str, Any], ...]:
    """Отделяет оборудование от портов и подтверждает бренд только дословным текстом.

    Строки текстового слоя дополняют VLM, но не назначают производителя по модели.
    Области изображения, параметры и физические обозначения VLM сохраняются.
    """
    explicit = text_equipment_records(text)
    by_model = {identity_token(row["model"]): row for row in explicit}
    grounded: list[dict[str, Any]] = []
    represented = set()
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        row["parameters"] = [
            dict(parameter)
            | (
                {}
                if _quantity_visible(
                    parameter, str(parameter.get("evidence_text") or "")
                )
                and (not text.strip() or _quantity_visible(parameter, text))
                else {"evidence_status": "needs_review"}
            )
            for parameter in row.get("parameters") or []
            if isinstance(parameter, Mapping)
        ]
        model = str(row.get("model") or "").strip()
        manufacturer = str(row.get("manufacturer") or "").strip()
        context = " ".join(
            str(row.get(field) or "") for field in ("equipment_type", "evidence_text")
        )
        non_equipment = (
            row.get("entity_kind") in {"port", "interface", "line", "symbol"}
            or bool(_NON_EQUIPMENT.search(context.strip()))
            or identity_token(model) in {"touchmemory", "rs485", "2rs485"}
        )
        # Модель ошибочно попала в manufacturer, а исполнение — в model.
        definition = by_model.get(identity_token(model))
        if definition is None and identity_token(manufacturer) in by_model:
            candidate = by_model[identity_token(manufacturer)]
            if identity_token(model) == identity_token(candidate["variant"]):
                definition = candidate
                model, manufacturer = candidate["model"], ""
                row["variant"] = candidate["variant"]
                row["variant_kind"] = candidate["variant_kind"]
                non_equipment = False
        if non_equipment:
            row.update(
                entity_kind="port",
                status="needs_review",
                identification_reason="not_equipment",
            )
            grounded.append(row)
            continue
        if definition is not None:
            model = definition["model"]
            row["variant_kind"] = definition["variant_kind"]
            if not row.get("variant") or identity_token(
                str(row["variant"])
            ) == identity_token(definition["variant"]):
                row["variant"] = definition["variant"]
            represented.add(
                (identity_token(model), identity_token(str(row.get("variant") or "")))
            )
        evidence = str(row.get("manufacturer_evidence_text") or "").strip()
        brand_proven = bool(
            manufacturer
            and evidence
            and contains_identity(evidence, manufacturer)
            and contains_identity(evidence, model)
            and bool(text.strip())
            and identity_token(evidence) in identity_token(text)
        )
        model_proven = definition is not None or (
            bool(model)
            and contains_identity(str(row.get("evidence_text") or ""), model)
            and row.get("status") == "resolved"
        )
        row.update(
            manufacturer=manufacturer if brand_proven else "",
            model=model,
            entity_kind="equipment",
            model_status="confirmed" if model_proven else "needs_review",
            manufacturer_status="explicit" if brand_proven else "unknown",
            status="resolved" if brand_proven and model_proven else "needs_review",
            identification_reason=""
            if brand_proven and model_proven
            else ("manufacturer_unknown" if model_proven else "model_uncertain"),
        )
        grounded.append(row)
    for definition in explicit:
        key = (
            identity_token(definition["model"]),
            identity_token(definition["variant"]),
        )
        if key not in represented:
            grounded.append(definition)
            represented.add(key)
    return tuple(grounded[:limit])
