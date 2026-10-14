# services/analysis-service/src/pdrd_analysis_service/application/graphical_refinement.py

"""Выборочное повторное чтение неоднозначных графических фактов Understanding.

Чёткий лист не вызывает дополнительных inference. Неудачное уточнение оставляет
исходный факт; успешное добавляется со связью происхождения и исходными координатами.
"""

import json
import logging
import re
from dataclasses import asdict, dataclass, replace

from pdrd_analysis_service.application.ports.image_cropper import ImageCropper
from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
    VisionModelError,
)
from pdrd_analysis_service.core.observability import log_execution_time
from pdrd_analysis_service.domain.analysis import PageFacts

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RefineGraphicalFacts:
    """Уточняет до заданного числа фрагментов с явным сигналом нечитаемости."""

    vision_model: StructuredVisionModel
    cropper: ImageCropper
    max_crops: int = 2

    @log_execution_time(operation="understanding.refine_graphics")
    async def execute(
        self, facts: PageFacts, *, page_number: int, image_bytes: bytes
    ) -> PageFacts:
        """Сохраняет все исходные факты и добавляет только привязанные уточнения."""
        additions = []
        attempted = 0
        if facts.page_type.casefold() not in {"drawing", "scheme", "diagram", "plan"}:
            return facts
        for index, fact in enumerate(facts.document_facts, 1):
            if attempted >= self.max_crops:
                break
            if not fact.identifier or not re.search(
                r"\?|неразбор|нечита|неясн",
                fact.value_raw + " " + fact.evidence_text,
                re.I,
            ):
                continue
            region = next(
                (r for r in fact.visual_regions if 0 < r.get("confidence", 0) < 0.7),
                None,
            )
            if region is None:
                continue
            attempted += 1
            try:
                crop = await self.cropper.crop(image_bytes, region)
                response = await self.vision_model.generate_json(
                    prompt="Прочитай только неоднозначный факт на фрагменте. Не ищи нарушения. "
                    "Объект, свойство и условия менять нельзя. Если значение не читается, readable=false. "
                    "Верни дословные value_raw/evidence_text и область доказательства "
                    "в координатах ФРАГМЕНТА 0..1000.\n"
                    + json.dumps(asdict(fact), ensure_ascii=False),
                    schema={
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "readable": {"type": "boolean"},
                            "value_raw": {"type": "string"},
                            "evidence_text": {"type": "string"},
                            "region": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    **{
                                        k: {
                                            "type": "integer",
                                            "minimum": 0,
                                            "maximum": 1000,
                                        }
                                        for k in ("x_min", "y_min", "x_max", "y_max")
                                    },
                                    "confidence": {
                                        "type": "number",
                                        "minimum": 0,
                                        "maximum": 1,
                                    },
                                },
                                "required": [
                                    "x_min",
                                    "y_min",
                                    "x_max",
                                    "y_max",
                                    "confidence",
                                ],
                            },
                        },
                        "required": [
                            "readable",
                            "value_raw",
                            "evidence_text",
                            "region",
                        ],
                    },
                    num_predict=1200,
                    seed=810 + index,
                    stage=f"graphical_refinement:{page_number}:{index}",
                    image_bytes=crop.image_bytes,
                )
            except (ValueError, OSError, VisionModelError):
                logger.info(
                    "graphical_refinement page=%s fact=%s outcome=unreadable",
                    page_number,
                    index,
                )
                continue
            payload = response.payload
            mapped = crop.project_region(payload.get("region", {}))
            value = str(payload.get("value_raw", "")).strip()
            evidence = str(payload.get("evidence_text", "")).strip()
            if (
                payload.get("readable") is not True
                or mapped is None
                or not value
                or not evidence
            ):
                continue
            additions.append(
                replace(
                    fact,
                    value_raw=value,
                    evidence_text=evidence,
                    visual_regions=(mapped,),
                    source_item_id=f"p{page_number:04d}-f{index:04d}",
                    source_pages=(page_number,),
                    extraction_method="vlm_crop",
                )
            )
        if attempted:
            logger.info(
                "graphical_refinement page=%s calls=%s resolved=%s",
                page_number,
                attempted,
                len(additions),
            )
        return replace(facts, document_facts=(*facts.document_facts, *additions))
