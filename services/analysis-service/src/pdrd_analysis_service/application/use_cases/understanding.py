# services/analysis-service/src/pdrd_analysis_service/application/use_cases/understanding.py

"""Сценарий извлечения структурированных сведений инженерного листа."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, replace

from pdrd_analysis_service.application.graphical_refinement import RefineGraphicalFacts
from pdrd_analysis_service.application.ports.image_cropper import ImageCropper
from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
    VisionModelOutputBudgetError,
)
from pdrd_analysis_service.application.specification_refinement import (
    RefineSpecification,
)
from pdrd_analysis_service.application.understanding_contract import (
    build_understanding_contract,
)
from pdrd_analysis_service.application.understanding_metrics import (
    UnderstandingMeasurements,
)
from pdrd_analysis_service.application.understanding_recovery import (
    limited_understanding_payload,
)
from pdrd_analysis_service.application.use_cases.common import (
    string_tuple,
)
from pdrd_analysis_service.application.vision_execution import AnalysisCancelledError
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    PageFacts,
)
from pdrd_analysis_service.domain.document_context import document_fact_from_mapping
from pdrd_analysis_service.domain.engineering_facts import (
    merge_text_engineering_facts,
)
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
            facts, _, limited_reason = await replace(
                self, vision_model=measurements
            )._execute(
                page_number=page_number,
                heuristic_page_type=heuristic_page_type,
                extracted_text=extracted_text,
                image_bytes=image_bytes,
                use_document_context=use_document_context,
                use_equipment_web_search=use_equipment_web_search,
                equipment_specification=equipment_specification,
                should_stop=should_stop,
            )
        except (Exception, asyncio.CancelledError) as error:
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
                "cancelled"
                if isinstance(
                    error,
                    (
                        SpecificationCancelled,
                        AnalysisCancelledError,
                        asyncio.CancelledError,
                    ),
                )
                else "failed"
            )
            diagnostics["error_code"] = (
                "cancelled"
                if diagnostics["result"] == "cancelled"
                else "output_budget"
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
        diagnostics["result"] = "incomplete" if limited_reason else "completed"
        diagnostics["limited_reason"] = limited_reason
        raw_rows = sum(
            len(group.get("rows", []))
            for group in facts.visual_specification_rows
            if isinstance(group.get("rows"), list)
        )
        accepted_rows = len(
            {
                fact.source_item_id
                for fact in facts.document_facts
                if fact.extraction_method == "vlm_specification_row"
            }
        )
        if use_equipment_web_search and not use_document_context:
            accepted_rows = len(
                {
                    item.get("specification_item_id")
                    for item in facts.equipment_identities
                    if item.get("extraction_method") == "vlm_specification_row"
                }
            )
        diagnostics.update(
            visual_rows=raw_rows,
            visual_rows_accepted=accepted_rows,
            visual_rows_unresolved=max(0, raw_rows - accepted_rows),
        )
        if raw_rows > accepted_rows:
            diagnostics["result"] = "incomplete"
            diagnostics["limited_reason"] = (
                limited_reason or "specification_rows_unbound"
            )
        if (
            (use_document_context or use_equipment_web_search)
            and not facts.equipment_specification
            and "спецификац" in facts.page_type.casefold()
            and raw_rows == 0
        ):
            diagnostics["result"] = "incomplete"
            diagnostics["limited_reason"] = (
                limited_reason or "specification_rows_not_extracted"
            )
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
                fact.extraction_method in {"pdf_table", "pdf_text"}
                for fact in document_facts
            ),
            "text_facts": sum(
                fact.extraction_method == "pdf_text" for fact in document_facts
            ),
            "vlm_facts": sum(
                fact.extraction_method not in {"pdf_table", "pdf_text"}
                for fact in document_facts
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
        GenerationMetrics | None,
        str | None,
    ]:
        """Делает один основной запрос; переполнение оставляет только программные факты."""
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
                return (
                    replace(
                        table_facts,
                        equipment_identities=(
                            table_equipment_records(specification, page_number)
                            if use_equipment_web_search
                            else ()
                        ),
                    ),
                    GenerationMetrics(0, "pdf_table", 0, 0, 0, 0, 0, 0, 0),
                    None,
                )
            if isinstance(content.get("outside_text"), str):
                extracted_text = content["outside_text"]
            prompt_suffix = (
                "\nТекстовая спецификация уже прочитана программно. Анализируй только сведения вне таблицы. "
                "Не перечисляй её оборудование, строки, количества или параметры повторно. "
                "Табличные факты D/EQ будут добавлены из исходных столбцов. Области таблиц: "
                + json.dumps(content.get("table_regions", []), ensure_ascii=False)
            )
        max_equipment = 40 if use_equipment_web_search and not tabular else 0
        max_graphical_targets = (
            self.max_graphical_crops if self.image_cropper is not None else 0
        )
        limited_reason = None
        prompt, schema = build_understanding_contract(
            page_number=page_number,
            heuristic_page_type=heuristic_page_type,
            extracted_text=extracted_text,
            max_facts=max_facts,
            max_equipment=max_equipment,
            max_graphical_targets=max_graphical_targets,
        )
        try:
            result = await self.vision_model.generate_json(
                prompt=prompt + prompt_suffix,
                schema=schema,
                num_predict=self.num_predict,
                seed=100,
                stage=(f"page_understanding:{page_number}"),
                image_bytes=image_bytes,
            )

            if result.metrics is not None and result.metrics.done_reason == "length":
                raise VisionModelOutputBudgetError(
                    "Ответ Understanding достиг предела вывода.",
                    metrics=result.metrics,
                    completed_fields={
                        key: value
                        for key, value in result.payload.items()
                        if key in {"specification_rows", "specification_notes"}
                        and isinstance(value, list)
                    },
                )
            payload, metrics = result.payload, result.metrics
            core = limited_understanding_payload(heuristic_page_type)
            if not payload or not any(payload.values()):
                limited_reason = "empty_page_facts"
            elif any(name not in payload or payload[name] is None for name in core):
                limited_reason = "partial_page_facts"
        except VisionModelOutputBudgetError as error:
            payload = {
                **limited_understanding_payload(heuristic_page_type),
                **error.completed_fields,
            }
            metrics = error.metrics
            limited_reason = "output_budget"

        if limited_reason and should_stop is not None and await should_stop():
            raise SpecificationCancelled

        facts = PageFacts(
            discipline=str(payload.get("discipline") or "").strip(),
            page_type=(
                str(payload.get("page_type") or "").strip() or heuristic_page_type
            ),
            summary=str(payload.get("summary") or "").strip(),
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
            graphical_targets=tuple(
                target
                for target in (payload.get("graphical_targets") or [])[
                    :max_graphical_targets
                ]
                if isinstance(target, dict)
            ),
            document_facts=tuple(
                fact
                for raw in (payload.get("document_facts") or [])[:max_facts]
                if (fact := document_fact_from_mapping(raw)) is not None
            ),
        )

        if (use_document_context or use_equipment_web_search) and not tabular:
            from pdrd_analysis_service.domain.visual_specification import (
                visual_equipment_records,
                visual_note_facts,
                visual_specification_facts,
            )

            visual_facts = visual_specification_facts(
                payload.get("specification_rows"), page_number
            )
            facts = replace(
                facts,
                visual_specification_notes=tuple(
                    note
                    for note in (payload.get("specification_notes") or [])
                    if isinstance(note, dict)
                ),
                visual_specification_rows=tuple(
                    row
                    for row in (payload.get("specification_rows") or [])
                    if isinstance(row, dict)
                ),
                document_facts=(
                    *facts.document_facts,
                    *(visual_facts if use_document_context else ()),
                    *(
                        visual_note_facts(
                            payload.get("specification_notes"), page_number
                        )
                        if use_document_context
                        else ()
                    ),
                ),
                equipment_identities=(
                    *facts.equipment_identities,
                    *(
                        visual_equipment_records(visual_facts)
                        if use_equipment_web_search
                        else ()
                    ),
                ),
            )

        if specification is not None:
            specification["refinement_metrics"] = refinement_metrics

        if table_facts is not None:
            facts = merge_specification_facts(table_facts, facts)

        if use_document_context or limited_reason:
            facts = replace(
                facts,
                document_facts=merge_text_engineering_facts(
                    facts.document_facts, extracted_text, page_number
                ),
            )

        if self.image_cropper is not None and self.max_graphical_crops > 0:
            facts = await RefineGraphicalFacts(
                self.vision_model,
                self.image_cropper,
                self.max_graphical_crops,
            ).execute(
                facts,
                page_number=page_number,
                image_bytes=image_bytes,
                should_stop=should_stop,
            )

        return (
            facts,
            metrics,
            limited_reason,
        )
