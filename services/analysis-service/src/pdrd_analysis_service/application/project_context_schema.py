# services/analysis-service/src/pdrd_analysis_service/application/project_context_schema.py

"""JSON Schema классификации диапазона ПЗ."""

from typing import Any


def build_project_context_classification_schema(
    page_numbers: tuple[
        int,
        ...,
    ],
) -> dict[str, Any]:
    """Строит strict JSON Schema для VLM."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "pages": {
                "type": "array",
                "minItems": len(
                    page_numbers,
                ),
                "maxItems": len(
                    page_numbers,
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "page": {
                            "type": "integer",
                            "enum": list(
                                page_numbers,
                            ),
                        },
                        "kind": {
                            "type": "string",
                            "enum": [
                                "explanatory_note",
                                "drawing",
                                "specification",
                                "other",
                            ],
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "reason": {
                            "type": "string",
                            "maxLength": 250,
                        },
                    },
                    "required": [
                        "page",
                        "kind",
                        "confidence",
                        "reason",
                    ],
                },
            }
        },
        "required": [
            "pages",
        ],
    }
