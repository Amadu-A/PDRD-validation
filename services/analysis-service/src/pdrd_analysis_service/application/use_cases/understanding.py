# services/analysis-service/src/pdrd_analysis_service/application/use_cases/understanding.py

"""Сценарий извлечения структурированных сведений инженерного листа."""

import json
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, replace

from pdrd_analysis_service.application.graphical_refinement import RefineGraphicalFacts
from pdrd_analysis_service.application.json_schemas import (
    build_page_facts_schema,
)
from pdrd_analysis_service.application.ports.image_cropper import ImageCropper
from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
    VisionModelOutputBudgetError,
)
from pdrd_analysis_service.application.prompts import (
    build_page_understanding_prompt,
)
from pdrd_analysis_service.application.specification_refinement import (
    RefineSpecification,
)
from pdrd_analysis_service.application.understanding_metrics import (
    UnderstandingMeasurements,
)
from pdrd_analysis_service.application.understanding_recovery import (
    recover_page_understanding,
)
from pdrd_analysis_service.application.use_cases.common import (
    string_tuple,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    PageFacts,
)
from pdrd_analysis_service.domain.document_context import document_fact_from_mapping
from pdrd_analysis_service.domain.equipment_identification import (
    ground_equipment_records,
)
from pdrd_analysis_service.domain.equipment_specification import (
    SpecificationCancelled,
    SpecificationContractError,
    table_equipment_records,
    validated_specification,
)
from pdrd_analysis_service.domain.specification_facts import (
    merge_specification_facts,
    specification_document_facts,
    specification_page_facts,
)


@dataclass(frozen=True, slots=True)
class UnderstandPage:
    """Получает факты листа без поиска нарушений."""

    vision_model: StructuredVisionModel

    num_predict: int
    max_facts_per_page: int = 12
    image_cropper: ImageCropper | None = None
    max_graphical_crops: int = 2

    async def execute(
        self,
        *,
        page_number: int,
        heuristic_page_type: str,
        extracted_text: str,
        image_bytes: bytes,
        use_document_context: bool = True,
        use_equipment_web_search: bool = False,
        equipment_specification: dict | None = None,
        should_stop: Callable[[], Awaitable[bool]] | None = None,
    ) -> tuple[PageFacts, GenerationMetrics]:
        """Выбирает существующий визуальный или программный маршрут и измеряет все обращения."""
        started = time.perf_counter()
        measurements = UnderstandingMeasurements(self.vision_model)
        try:
            facts, _ = await replace(self, vision_model=measurements)._execute(
                page_number=page_number,
                heuristic_page_type=heuristic_page_type,
                extracted_text=extracted_text,
                image_bytes=image_bytes,
                use_document_context=use_document_context,
                use_equipment_web_search=use_equipment_web_search,
                equipment_specification=equipment_specification,
                should_stop=should_stop,
            )
        except Exception as error:
            failed_specification = {}
            with suppress(SpecificationContractError):
                failed_specification = (
                    validated_specification(equipment_specification, page_number) or {}
                )
            diagnostics = self._diagnostics(
                page_number,
                use_document_context,
                use_equipment_web_search,
                failed_specification,
                (),
                started,
            )
            diagnostics["result"] = (
                "cancelled" if isinstance(error, SpecificationCancelled) else "failed"
            )
            diagnostics["error_code"] = (
                "output_budget"
                if isinstance(error, VisionModelOutputBudgetError)
                else "stage_error"
            )
            measurements.result(diagnostics)
            raise
        diagnostics = self._diagnostics(
            page_number,
            use_document_context,
            use_equipment_web_search,
            facts.equipment_specification or {},
            facts.document_facts,
            started,
        )
        diagnostics["result"] = "completed"
        return facts, measurements.result(diagnostics)

    @staticmethod
    def _diagnostics(
        page_number,
        use_document_context,
        use_equipment_web_search,
        spec,
        document_facts,
        started,
    ):
        """Сохраняет счётчики и фиксированные причины без текста документа или ответа VLM."""
        reliable = (
            spec.get("status") == "reliable" and spec.get("coverage_complete") is True
        )
        content = spec.get("page_content")
        content = content if isinstance(content, dict) else {}
        route = (
            "pdf_table"
            if reliable
            and content.get("version") == 1
            and content.get("route") == "pdf_table"
            and content.get("requires_vision") is False
            else "mixed"
            if reliable
            else "vision"
        )
        reason = content.get("reason")
        safe_reason = (
            reason
            if isinstance(reason, str)
            and reason
            in {
                "non_table_content",
                "page_content_inspection_failed",
            }
            else "page_content_metadata_missing"
        )
        return {
            "page_number": page_number,
            "use_document_context": use_document_context,
            "use_equipment_web_search": use_equipment_web_search,
            "route": route,
            "specification_status": (
                "reliable" if reliable else "unreliable" if spec else "absent"
            ),
            "coverage_complete": spec.get("coverage_complete") is True,
            "raw_rows": spec.get("raw_row_count", 0),
            "items": len(spec.get("items", [])),
            "document_facts": len(document_facts),
            "programmatic_facts": sum(
                fact.extraction_method == "pdf_table" for fact in document_facts
            ),
            "vlm_facts": sum(
                fact.extraction_method != "pdf_table" for fact in document_facts
            ),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "fallback_reason": safe_reason
            if route == "mixed"
            else "unreliable_or_absent_table"
            if route == "vision"
            else None,
        }

    async def _execute(
        self,
        *,
        page_number: int,
        heuristic_page_type: str,
        extracted_text: str,
        image_bytes: bytes,
        use_document_context: bool = True,
        use_equipment_web_search: bool = False,
        equipment_specification: dict | None = None,
        should_stop: Callable[[], Awaitable[bool]] | None = None,
    ) -> tuple[
        PageFacts,
        GenerationMetrics,
    ]:
        """Извлекает сведения страницы; при выключенном D использует лёгкие промпт и схему."""
        max_facts = self.max_facts_per_page if use_document_context else 0
        specification = (
            validated_specification(equipment_specification, page_number)
            if use_equipment_web_search or use_document_context
            else None
        )
        tabular = bool(
            specification
            and specification.get("status") == "reliable"
            and specification.get("coverage_complete") is True
        )
        needs_refinement = tabular and any(
            item.get("needs_review") for item in specification["items"]
        )
        refinement_metrics = (
            await RefineSpecification(self.vision_model).execute(
                specification, should_stop=should_stop
            )
            if tabular
            else []
        )
        if needs_refinement and should_stop is not None and await should_stop():
            raise SpecificationCancelled
        table_facts = None
        prompt_suffix = ""
        if tabular:
            table_facts = specification_page_facts(
                specification,
                document_facts=(
                    specification_document_facts(specification, page_number)
                    if use_document_context
                    else ()
                ),
            )
            content = specification.get("page_content")
            content = content if isinstance(content, dict) else {}
            if (
                content.get("version") == 1
                and content.get("route") == "pdf_table"
                and content.get("requires_vision") is False
            ):
                specification["refinement_metrics"] = refinement_metrics
                return replace(
                    table_facts,
                    equipment_identities=(
                        table_equipment_records(specification, page_number)
                        if use_equipment_web_search
                        else ()
                    ),
                ), GenerationMetrics(0, "pdf_table", 0, 0, 0, 0, 0, 0, 0)
            if isinstance(content.get("outside_text"), str):
                extracted_text = content["outside_text"]
            prompt_suffix = (
                "\nТекстовая спецификация уже прочитана программно. Анализируй только сведения вне таблицы. "
                "Не перечисляй её оборудование, строки, количества или параметры повторно. "
                "Табличные факты D/EQ будут добавлены из исходных столбцов. Области таблиц: "
                + json.dumps(content.get("table_regions", []), ensure_ascii=False)
            )
        max_equipment = 40 if use_equipment_web_search and not tabular else 0
        try:
            result = await self.vision_model.generate_json(
                prompt=(
                    build_page_understanding_prompt(
                        page_number=page_number,
                        heuristic_page_type=(heuristic_page_type),
                        extracted_text=(extracted_text),
                        max_facts=max_facts,
                        max_equipment=max_equipment,
                    )
                    + prompt_suffix
                ),
                schema=(build_page_facts_schema(max_facts, max_equipment)),
                num_predict=self.num_predict,
                seed=100,
                stage=(f"page_understanding:{page_number}"),
                image_bytes=image_bytes,
            )

            if result.metrics is not None and result.metrics.done_reason == "length":
                raise VisionModelOutputBudgetError(
                    "Ответ Understanding достиг предела вывода.", metrics=result.metrics
                )
            payload, metrics = result.payload, result.metrics
        except VisionModelOutputBudgetError as error:
            payload, metrics = await recover_page_understanding(
                self.vision_model,
                page_number=page_number,
                heuristic_page_type=heuristic_page_type,
                extracted_text=extracted_text,
                image_bytes=image_bytes,
                num_predict=self.num_predict,
                max_facts=max_facts,
                max_equipment=max_equipment,
                initial_error=error,
                should_stop=should_stop,
                prompt_suffix=prompt_suffix,
            )

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
            equipment_identities=(
                table_equipment_records(specification, page_number)
                if tabular and use_equipment_web_search
                else ground_equipment_records(
                    payload.get("equipment_identities", []), extracted_text
                )
                if use_equipment_web_search
                else ()
            ),
            equipment_specification=specification,
            document_facts=tuple(
                fact
                for raw in payload.get("document_facts", [])[:max_facts]
                if (fact := document_fact_from_mapping(raw)) is not None
            ),
        )

        if specification is not None:
            specification["refinement_metrics"] = refinement_metrics

        if table_facts is not None:
            facts = merge_specification_facts(table_facts, facts)

        if self.image_cropper is not None and self.max_graphical_crops > 0:
            facts = await RefineGraphicalFacts(
                self.vision_model,
                self.image_cropper,
                self.max_graphical_crops,
            ).execute(facts, page_number=page_number, image_bytes=image_bytes)

        return (
            facts,
            metrics,
        )
