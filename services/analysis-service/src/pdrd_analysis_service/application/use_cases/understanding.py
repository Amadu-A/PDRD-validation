# services/analysis-service/src/pdrd_analysis_service/application/use_cases/understanding.py

"""Use case structured understanding инженерного листа."""

from dataclasses import dataclass

from pdrd_analysis_service.application.json_schemas import (
    build_page_facts_schema,
)
from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
)
from pdrd_analysis_service.application.prompts import (
    build_page_understanding_prompt,
)
from pdrd_analysis_service.application.use_cases.common import (
    string_tuple,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    PageFacts,
)
from pdrd_analysis_service.domain.document_context import document_fact_from_mapping


@dataclass(frozen=True, slots=True)
class UnderstandPage:
    """Получает факты листа без поиска нарушений."""

    vision_model: StructuredVisionModel

    num_predict: int
    max_facts_per_page: int = 12

    async def execute(
        self,
        *,
        page_number: int,
        heuristic_page_type: str,
        extracted_text: str,
        image_bytes: bytes,
    ) -> tuple[
        PageFacts,
        GenerationMetrics,
    ]:
        """Выполняет structured VLM understanding."""
        result = await self.vision_model.generate_json(
            prompt=(
                build_page_understanding_prompt(
                    page_number=page_number,
                    heuristic_page_type=(heuristic_page_type),
                    extracted_text=(extracted_text),
                    max_facts=self.max_facts_per_page,
                )
            ),
            schema=(build_page_facts_schema(self.max_facts_per_page)),
            num_predict=self.num_predict,
            seed=100,
            stage=(f"page_understanding:{page_number}"),
            image_bytes=image_bytes,
        )

        payload = result.payload

        facts = PageFacts(
            discipline=str(
                payload.get(
                    "discipline",
                    "",
                )
            ).strip(),
            page_type=(
                str(
                    payload.get(
                        "page_type",
                        "",
                    )
                ).strip()
                or heuristic_page_type
            ),
            summary=str(
                payload.get(
                    "summary",
                    "",
                )
            ).strip(),
            objects=string_tuple(
                payload.get(
                    "objects",
                ),
                limit=15,
            ),
            connections=string_tuple(
                payload.get(
                    "connections",
                ),
                limit=12,
            ),
            labels=string_tuple(
                payload.get(
                    "labels",
                ),
                limit=15,
            ),
            normative_queries=string_tuple(
                payload.get(
                    "normative_queries",
                ),
                limit=6,
            ),
            document_facts=tuple(
                fact
                for raw in payload.get("document_facts", [])[: self.max_facts_per_page]
                if (fact := document_fact_from_mapping(raw)) is not None
            ),
        )

        return (
            facts,
            result.metrics,
        )
