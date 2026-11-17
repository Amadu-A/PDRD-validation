# services/document-service/src/pdrd_document_service/domain/external_specification.py

"""Связь авторитетных строк Spec Extracting AI с доказанной геометрией исходного PDF."""

import re
import unicodedata
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from pdrd_document_service.domain.specification_geometry import bind_pdf_row
from pdrd_document_service.domain.specification_parameters import (
    specification_parameters,
)

FIELDS = (
    "Caption",
    "Units",
    "Quantity",
    "UserComment1",
    "Pages",
    "Brand",
    "Article",
    "Note",
)


def _key(value: str) -> str:
    """Сопоставляет оформление текста без изменения исходных значений экспорта."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


def _same_quantity(left: str, right: str) -> bool:
    """Не превращает десятичные 150,0 в 1500 при сопоставлении физических строк."""
    try:
        a, b = (Decimal(_key(value).replace(",", ".")) for value in (left, right))
        return a.is_finite() and b.is_finite() and a == b
    except InvalidOperation:
        return bool(left) and _key(left) == _key(right)


def bind_external_specification(
    *,
    page_number: int,
    native: dict | None,
    extraction: dict,
    job_id: str,
    artifact_key: str,
    warnings: tuple,
) -> dict:
    """Сохраняет каждую строку; неоднозначная геометрия не удаляет позицию."""
    native = native or {}
    items, rows, used = [], [], set()
    external = extraction["items"]
    for ordinal, original in enumerate(external, 1):
        if int(original["Pages"]) != page_number:
            continue
        item_id = f"spec-{artifact_key[:16]}-{ordinal:06d}"
        matches = [
            item
            for item in native.get("items", [])
            if _key(item.get("Caption", "")) == _key(original["Caption"])
            and _key(item.get("Units", "")) == _key(original["Units"])
            and _same_quantity(item.get("Quantity", ""), original["Quantity"])
            and page_number in item.get("source_pages", [])
            and all(
                not original[field]
                or " ".join(original[field].casefold().split())
                == " ".join(
                    str(item.get(field) or item.get(normalized) or "")
                    .casefold()
                    .split()
                )
                for field, normalized in (("Brand", "model"), ("Article", "article"))
            )
        ]
        # Даже совпадающий текст двух физических строк не доказывает, какая из
        # них обозначена внешней записью. Сохраняем обе записи без ложного bbox.
        duplicates = sum(
            int(other["Pages"]) == page_number
            and _same_quantity(other["Quantity"], original["Quantity"])
            and all(
                _key(other[k]) == _key(original[k])
                for k in ("Caption", "Units", "Brand", "Article")
            )
            for other in external
        )
        match = (
            matches[0]
            if len(matches) == 1
            and duplicates == 1
            and matches[0]["item_id"] not in used
            else None
        )
        regions = []
        if match:
            regions = [
                {**region, "row_id": item_id}
                for region in match.get("source_regions", [])
                if region["page"] == page_number
            ]
            used.add(match["item_id"])
        elif duplicates == 1:
            regions = [
                {"page": page_number, "row_id": item_id, **box}
                for box in bind_pdf_row(original, native)
            ]
        raw_cells = [{"column": key, "text": original[key]} for key in FIELDS]
        unresolved = not regions
        rows.append(
            {
                "row_id": item_id,
                "page": page_number,
                "kind": "item",
                "raw_cells": raw_cells,
                "bbox": regions[0] if regions else None,
                "geometry_status": "geometry_unresolved" if unresolved else "resolved",
            }
        )
        parameters = deepcopy(match.get("parameters", [])) if match else []
        parameters.extend(
            value
            for value in specification_parameters(original["Caption"])
            if value not in parameters
        )
        item = {
            "position": match.get("position", "") if match else "",
            "name": original["Caption"],
            "model": original["Brand"]
            or (
                match.get("model", "")
                if match and not match.get("needs_review")
                else ""
            ),
            "manufacturer": match.get("manufacturer", "") if match else "",
            "article": original["Article"]
            or (match.get("article", "") if match else ""),
            "variant": match.get("variant", "") if match else "",
            "units": original["Units"],
            "quantity": original["Quantity"],
            "project_code": original["UserComment1"],
            "under": match.get("under", "") if match else "",
            "Under": match.get("under", "") if match else "",
            "note": original["Note"],
            "item_id": item_id,
            "row_ids": [item_id],
            "source_pages": [page_number],
            "source_regions": regions,
            "raw_values": [raw_cells],
            "needs_review": False,
            "geometry_status": "geometry_unresolved" if unresolved else "resolved",
            "extraction_method": "spec_service",
            "external_original": deepcopy(original),
            "parameters": parameters,
            **original,
        }
        items.append(item)
    content = deepcopy(native.get("page_content", {}))
    # Чтение состава таблицы завершено независимо от локализации её строк.
    # Отдельные схемы/нетабличная часть остаются в старом маршруте понимания.
    return {
        "version": 1,
        "page": page_number,
        "status": "reliable",
        "coverage_complete": True,
        "project_code": items[0]["UserComment1"] if items else "",
        "items": items,
        "raw_rows": rows,
        "raw_row_count": len(rows),
        "page_content": content,
        "external_extraction": {
            "provider": "spec_extracting_ai",
            "job_id": job_id,
            "artifact_key": artifact_key,
            "item_count": len(external),
            "warnings": list(warnings),
        },
        "warnings": [],
    }
