# services/analysis-service/src/pdrd_analysis_service/application/use_cases/normative.py

"""Use cases retrieval preparation и инженерной проверки листа."""

import logging
from dataclasses import dataclass
from typing import Any

from pdrd_analysis_service.application.json_schemas import (
    build_normative_check_schema,
)
from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
)
from pdrd_analysis_service.application.prompts import (
    build_experience_query,
    build_normative_check_prompt,
)
from pdrd_analysis_service.application.use_cases.common import (
    ViolationCandidateSelection,
    build_basis,
    category,
    confidence,
    finding_status,
    select_violation_candidates,
    severity,
    string_tuple,
)
from pdrd_analysis_service.application.use_cases.finding_visual_regions import (
    parse_finding_visual_regions,
)
from pdrd_analysis_service.domain.analysis import (
    FindingDraft,
    GenerationMetrics,
    NormativeSource,
    PageFacts,
    TechnicalAssignmentConflictCandidate,
    TechnicalAssignmentSource,
    UserPackageSource,
)

logger = logging.getLogger(
    "uvicorn.error",
)

_NORMATIVE_PROBE_BATCH_SIZE = 10
_NORMATIVE_PROBE_NUM_PREDICT = 4000
_NORMATIVE_MAX_PROBE_ROUNDS = 3
_NORMATIVE_DENSE_UNIQUE_RATIO = 0.70

_HIGH_RECALL_FINDING_POLICY = """
--- HIGH-RECALL FINDING POLICY ---

Проверка может выполняться несколькими последовательными
candidate batches.

Каждый текущий batch должен содержать только НОВЫЕ
различимые candidate findings.

Перед формированием JSON сделай полный проход по листу
и перечисляй каждую различимую проблему, для которой
есть конкретный визуальный или текстовый факт.

Если JSON Schema текущего вызова ограничивает размер массива,
это означает ёмкость ТЕКУЩЕГО batch, а не общий лимит анализа.

Если distinct candidates больше ёмкости текущего batch:
- заполни batch различимыми candidates;
- не повторяй один candidate несколько раз ради заполнения массива;
- следующие candidates будут запрошены continuation-вызовом.

Если новых distinct candidates меньше ёмкости batch,
верни только фактически найденные candidates.

Если новых distinct candidates нет:
violations=[].

НЕ выбирай только самые важные замечания.
НЕ ограничивай ответ несколькими примерами.
НЕ сокращай количество candidate findings ради более
короткого JSON, если в текущем batch ещё есть реальные
различимые проблемы.

Компактность достигается краткостью полей каждого candidate.

НЕ удаляй candidate только потому, что:

- у него ниже confidence, чем у другого candidate;
- для него не найден N/T/U source;
- он требует инженерной проверки;
- рядом уже есть похожее, но относящееся
  к другому объекту, обозначению или участку;
- нормативное основание пока не удалось подтвердить.

Если конкретный факт существует, но уверенности
недостаточно, сохрани candidate со status=needs_review.

При этом НЕ добавляй в violations подтверждения
соответствия и положительные результаты проверки.

Если проблемы нет, candidate не создавай.

Один физически различимый объект/участок/несоответствие
не объединяй с другим только ради сокращения ответа.

КРИТИЧЕСКИ ВАЖНО:

Не создавай несколько полностью одинаковых candidates
с одинаковыми comment и evidence.

Не перефразируй уже найденную проблему только для того,
чтобы она выглядела как новый candidate.

Если ниже передан список ALREADY FOUND DISTINCT CANDIDATES,
не возвращай ни один из них повторно.

Backend выполняет только безопасное exact consolidation
по normalized comment + evidence.
Semantic filtering после VLM не используется.

Формируй каждый candidate максимально компактно:

- comment: не более 160 символов, одна конкретная фраза;
- evidence: не более 180 символов, только наблюдаемый факт;
- recommendation_draft="" всегда; рекомендация будет
  сформирована отдельным этапом finalization;
- не повторяй один и тот же нормативный текст одновременно
  в comment и evidence;
- source IDs перечисляй только в соответствующих массивах.

BUSINESS CHECK MATRIX.

Для каждого листа применяй только реально относящиеся
к его дисциплине, типу и содержимому проверки:

1. НОРМОКОНТРОЛЬ И ОФОРМЛЕНИЕ:
   - внутренняя согласованность основной надписи,
     номеров листов, обозначений, шифров и марок;
   - состав и заполнение спецификаций;
   - внешние требования ГОСТ/СП применяй только
     при наличии прямого N-source.

2. ОБОРУДОВАНИЕ И УСЛОВИЯ СРЕДЫ:
   - IP, климатическое исполнение, температура,
     огнестойкость и свойства кабелей проверяй только,
     если условия и требуемая характеристика видимы
     на листе либо подтверждены N/T/U source;
   - не подставляй паспортные данные производителя
     или условия эксплуатации из памяти модели.

3. ЛОГИКА РАБОТЫ И СХЕМЫ:
   - АК: прослеживай цепочку
     датчик -> вход -> контроллер/алгоритм ->
     выход -> исполнительный механизм ->
     обратная связь и блокировки;
   - ЭОМ: проверяй согласованность
     источник -> защита -> кабель -> нагрузка,
     фазность и номиналы; селективность
     и падение напряжения оценивай только
     при достаточных исходных данных;
   - ПС/СКУД: прослеживай пожарный сигнал,
     управляющую команду, разблокировку,
     fail-safe и путь эвакуации;
   - СКС и другие слаботочные схемы:
     проверяй топологию, связь узлов,
     маркировку и внутреннюю согласованность;
   - не создавай finding только потому,
     что часть цепочки находится на другом листе,
     если текущий лист этого не опровергает.

4. ОПТИМИЗАЦИЯ И ДОСТУПНОСТЬ:
   - унификацию, замены брендов и доступность
     оценивай только при наличии T/U или другого
     явного проектного контекста;
   - не используй знания о рынке и брендах
     из памяти модели как доказательство.

5. СООТВЕТСТВИЕ ТЗ:
   - T-source является отдельным проектным требованием;
   - не смешивай T с нормативным N;
   - independent T-first остаётся основным exhaustive
     механизмом проверки требований ТЗ.

6. ТЕКСТОВЫЕ КОММЕНТАРИИ И ПОЯСНЕНИЯ:
   - проверяй внутреннюю достаточность описания
     алгоритмов, монтажа и смежных требований,
     только когда обязательность можно вывести
     из самого листа либо N/T/U context;
   - отсутствие внешней информации само по себе
     не является нарушением.

SEMANTIC EVIDENCE DISCIPLINE.

Визуальная форма, ориентация, заливка или стиль символа
сами по себе НЕ доказывают скрытую инженерную классификацию,
если её смысл не задан явно на текущем листе, в легенде
либо переданным N/T/U source.

Не выводи только по внешнему виду условного обозначения,
что объект является:
- наземным или подземным;
- рабочим или резервным;
- открытым или закрытым;
- конкретным типом, исполнением или состоянием оборудования.

Если такое значение прямо подписано, определено легендой
или подтверждено N/T/U source, используй его как evidence.

Если у одного символа остаются несколько правдоподобных
трактовок, не создавай finding, противоречие которого
возникает только после выбора одной из этих трактовок.

Для сравнительного finding отдельно проверь,
что сравниваются один и тот же объект/класс объектов,
одно и то же свойство и один и тот же смысловой scope.

TRANSPORT / PROJECT PAGE NUMBERING.

page_number, physical PDF page, физический индекс страницы
и аналогичные backend metadata НЕ являются автоматически
значением проектного поля "Лист" или "Листов".

Не создавай finding только потому,
что физический номер PDF-страницы отличается
от значения "Лист" в основной надписи.

Backend page_number используется для маршрутизации
и локализации результата, а не как инженерное evidence.

Два ВИДИМЫХ проектных номера допустимо сравнивать
только если сам документ явно показывает,
что они принадлежат одной системе проектной нумерации
и описывают одно и то же свойство.

QUANTITY / CHARACTERISTIC RELATIONSHIP.

Различное количество РАЗНЫХ типов оборудования
само по себе НЕ является противоречием.

Перед finding вида
"количество X не совпадает с количеством Y"
обязательно установи по самому листу или N/T/U,
что между X и Y существует конкретная обязательная связь:
например one-to-one, один комплект на объект,
заданная кратность или явно указанное равенство количества.

Без такой связи НЕ создавай finding только потому, что:
- насосов 3, а виброкомпенсаторов 4;
- насосов 3, а кранов 12;
- котлов 5, а арматуры другого типа 4;
- количества разных строк спецификации различаются.

То же относится к характеристикам разных объектов.

Разные значения IP, напряжения, диаметра, мощности,
давления, температуры и других параметров
НЕ являются противоречием сами по себе,
если не доказано, что это характеристика
ОДНОГО И ТОГО ЖЕ объекта или одно обязательное требование.

Если один и тот же tagged object действительно имеет
разные значения одного свойства в двух местах документа,
это допустимое основание для finding.

VISUAL EVIDENCE REGIONS.

Одновременно с каждым candidate сохрани место,
ГДЕ ИМЕННО на текущем изображении ты увидел evidence.

Поле visual_regions:
- массив от 0 до 4 прямоугольных областей;
- координаты нормализованы в диапазоне 0..1000;
- x_min/y_min — левый верхний угол;
- x_max/y_max — правый нижний угол;
- bbox должен быть максимально тесным вокруг
  конкретного текста, символа, узла, линии,
  таблицы или другого evidence;
- confidence относится именно к точности локализации;
- label кратко называет то, что находится в bbox.

Если finding сравнивает два или несколько
ДЕЙСТВИТЕЛЬНО СОПОСТАВИМЫХ мест одного листа,
верни отдельную visual_region
для КАЖДОГО сравниваемого места.

Например если один и тот же tagged object
имеет два явно различающихся значения
одной характеристики в двух таблицах,
верни region для каждого из этих значений.

НЕ локализуй finding по всем словам,
которые случайно встречаются в evidence.

Visual region должна указывать именно
на объект или участок, из-за которого создан finding.

Для графической ошибки допустим bbox линии,
узла или соединения даже без текста.

visual_regions=[] допустим только когда:
- finding относится к отсутствующему элементу
  и невозможно честно указать локальную область,
  где он должен находиться;
- evidence относится ко всему листу;
- точное место действительно нельзя определить
  по текущему изображению.

Не выдумывай координаты ради заполнения массива.

В PDF + CAD combined mode visual_regions
должны описывать evidence на ЛЕВОМ PDF-представлении
и использовать координатное пространство самого
PDF-представления 0..1000.

Если evidence существует только на CAD-render справа
и не имеет честной PDF-области,
верни visual_regions=[].

Ответственность за то, что в массив попадают именно
реальные distinct candidate findings, остаётся на этом этапе.

--- END HIGH-RECALL FINDING POLICY ---
""".strip()


def _candidate_batch_instruction(
    *,
    capacity: int,
    existing_candidates: tuple[
        dict[str, Any],
        ...,
    ],
) -> str:
    """Строит continuation instruction без semantic backend filtering."""
    if existing_candidates:
        existing_lines = [
            (
                f"{index}. "
                f"comment={str(candidate.get('comment', '')).strip()!r}; "
                f"evidence={str(candidate.get('evidence', '')).strip()!r}"
            )
            for index, candidate in enumerate(
                existing_candidates,
                start=1,
            )
        ]

        existing_text = "\n".join(
            existing_lines,
        )

    else:
        existing_text = "Список пуст: это первый candidate batch."

    return f"""
--- DISTINCT CANDIDATE BATCH ---

Ёмкость текущего batch: {capacity}.

Верни ТОЛЬКО новые distinct findings.

Уже найденные distinct candidates:

{existing_text}

Не повторяй их дословно.
Не перефразируй их как новые findings.
Не заполняй свободные места дублями.

Если после исключения уже найденных candidates
новых проблем меньше {capacity}, верни меньше.

Если новых проблем нет:
violations=[].

--- END DISTINCT CANDIDATE BATCH ---
""".strip()


def _sum_optional_ints(
    values: tuple[
        int | None,
        ...,
    ],
) -> int | None:
    """Суммирует available token counters нескольких VLM calls."""
    present = tuple(value for value in values if value is not None)

    if not present:
        return None

    return sum(
        present,
    )


def _combine_generation_metrics(
    metrics: list[GenerationMetrics,],
) -> GenerationMetrics:
    """Объединяет метрики adaptive VLM calls одного logical stage."""
    if not metrics:
        raise RuntimeError(
            "Adaptive normative discovery не выполнил ни одного VLM call.",
        )

    if len(metrics) == 1:
        return metrics[0]

    return GenerationMetrics(
        attempt=max(metric.attempt for metric in metrics),
        done_reason=metrics[-1].done_reason,
        requested_num_predict=sum(metric.requested_num_predict for metric in metrics),
        total_duration_ms=sum(metric.total_duration_ms for metric in metrics),
        load_duration_ms=sum(metric.load_duration_ms for metric in metrics),
        prompt_eval_count=_sum_optional_ints(
            tuple(metric.prompt_eval_count for metric in metrics)
        ),
        eval_count=_sum_optional_ints(tuple(metric.eval_count for metric in metrics)),
        content_length=sum(metric.content_length for metric in metrics),
        thinking_length=sum(metric.thinking_length for metric in metrics),
    )


@dataclass(frozen=True, slots=True)
class BuildNormativeQueries:
    """Строит retrieval queries без обращения к VLM."""

    max_queries: int = 7

    def execute(
        self,
        *,
        page_facts: PageFacts,
        extracted_text: str,
        project_context_texts: tuple[
            str,
            ...,
        ] = (),
    ) -> tuple[
        str,
        ...,
    ]:
        """Строит нейтральные запросы к Knowledge Service."""
        queries = [
            query.strip() for query in page_facts.normative_queries if query.strip()
        ]

        objects = "; ".join(
            page_facts.objects[:10],
        )

        connections = "; ".join(
            page_facts.connections[:8],
        )

        labels = "; ".join(
            page_facts.labels[:10],
        )

        pz_hint = " ".join(text[:300] for text in project_context_texts[:3])

        queries.append(
            (
                "Подобрать применимые требования "
                "для проверки инженерного листа. "
                f"Дисциплина: {page_facts.discipline}. "
                f"Тип листа: {page_facts.page_type}. "
                f"Содержание: {page_facts.summary}. "
                f"Объекты: {objects}. "
                f"Связи: {connections}. "
                f"Обозначения: {labels}. "
                f"Контекст ПЗ проекта: {pz_hint}"
            ).strip()
        )

        if not queries and extracted_text.strip():
            queries.append(
                "Подобрать применимые нормативные требования: " + extracted_text[:1800]
            )

        result: list[str,] = []

        seen: set[str,] = set()

        for query in queries:
            normalized = query.strip()

            if not normalized or normalized in seen:
                continue

            seen.add(
                normalized,
            )

            result.append(
                normalized,
            )

        return tuple(
            result[: self.max_queries],
        )


@dataclass(frozen=True, slots=True)
class CheckPageAgainstNorms:
    """Выполняет инженерную и N/T/U проверку одного листа."""

    vision_model: StructuredVisionModel

    num_predict: int

    max_issues: int

    normative_text_limit: int

    async def _discover_candidates(
        self,
        *,
        page_number: int,
        prompt: str,
        normative_source_ids: tuple[
            str,
            ...,
        ],
        technical_assignment_source_ids: tuple[
            str,
            ...,
        ],
        user_package_source_ids: tuple[
            str,
            ...,
        ],
        image_bytes: bytes,
    ) -> tuple[
        str,
        ViolationCandidateSelection,
        GenerationMetrics,
    ]:
        """Adaptive discovery не даёт дублям занять весь output budget."""
        raw_candidates: list[
            dict[
                str,
                Any,
            ]
        ] = []

        all_metrics: list[GenerationMetrics,] = []

        summary = ""

        candidate_selection = select_violation_candidates(
            [],
        )

        mode = "probe"
        probe_round = 0
        call_index = 0

        while candidate_selection.consolidated_count < self.max_issues:
            remaining_capacity = (
                self.max_issues - candidate_selection.consolidated_count
            )

            if mode == "bulk":
                current_capacity = remaining_capacity
                current_num_predict = self.num_predict
                stage_suffix = "bulk"

            else:
                probe_round += 1

                current_capacity = min(
                    _NORMATIVE_PROBE_BATCH_SIZE,
                    remaining_capacity,
                )

                current_num_predict = min(
                    _NORMATIVE_PROBE_NUM_PREDICT,
                    self.num_predict,
                )

                stage_suffix = f"probe{probe_round}"

            existing_candidates = candidate_selection.candidates

            batch_prompt = (
                f"{prompt}\n\n"
                f"{_HIGH_RECALL_FINDING_POLICY}\n\n"
                f"{
                    _candidate_batch_instruction(
                        capacity=current_capacity,
                        existing_candidates=existing_candidates,
                    )
                }"
            )

            generation = await self.vision_model.generate_json(
                prompt=batch_prompt,
                schema=build_normative_check_schema(
                    source_ids=normative_source_ids,
                    technical_assignment_source_ids=(technical_assignment_source_ids),
                    user_package_source_ids=(user_package_source_ids),
                    max_issues=current_capacity,
                ),
                num_predict=current_num_predict,
                seed=200 + call_index,
                stage=(f"normative_check:{page_number}:{stage_suffix}"),
                image_bytes=image_bytes,
            )

            call_index += 1

            all_metrics.append(
                generation.metrics,
            )

            if not summary:
                summary = str(
                    generation.payload.get(
                        "summary",
                        "",
                    )
                ).strip()

            violations = generation.payload.get(
                "violations",
                [],
            )

            round_selection = select_violation_candidates(
                violations,
            )

            if not isinstance(
                violations,
                list,
            ):
                raise ValueError(
                    "Поле violations должно быть JSON array.",
                )

            before_unique = candidate_selection.consolidated_count

            raw_candidates.extend(
                dict(
                    candidate,
                )
                for candidate in violations
            )

            candidate_selection = select_violation_candidates(
                raw_candidates,
            )

            new_unique = candidate_selection.consolidated_count - before_unique

            generated = round_selection.generated_count

            unique_ratio = new_unique / generated if generated > 0 else 0.0

            duplicate_ratio = 1.0 - unique_ratio if generated > 0 else 0.0

            logger.info(
                (
                    "normative_discovery_round "
                    "page=%s mode=%s "
                    "round=%s capacity=%s "
                    "num_predict=%s "
                    "generated=%s "
                    "round_distinct=%s "
                    "new_unique=%s "
                    "total_unique=%s "
                    "unique_ratio=%.3f "
                    "duplicate_ratio=%.3f"
                ),
                page_number,
                mode,
                probe_round,
                current_capacity,
                current_num_predict,
                generated,
                round_selection.consolidated_count,
                new_unique,
                candidate_selection.consolidated_count,
                unique_ratio,
                duplicate_ratio,
            )

            if candidate_selection.consolidated_count >= self.max_issues:
                logger.info(
                    (
                        "normative_discovery_stop "
                        "page=%s reason=max_issues "
                        "unique=%s raw=%s"
                    ),
                    page_number,
                    candidate_selection.consolidated_count,
                    len(
                        raw_candidates,
                    ),
                )

                break

            if generated < current_capacity:
                logger.info(
                    (
                        "normative_discovery_stop "
                        "page=%s reason=batch_not_full "
                        "generated=%s capacity=%s "
                        "unique=%s raw=%s"
                    ),
                    page_number,
                    generated,
                    current_capacity,
                    candidate_selection.consolidated_count,
                    len(
                        raw_candidates,
                    ),
                )

                break

            if new_unique == 0:
                logger.info(
                    (
                        "normative_discovery_stop "
                        "page=%s reason=no_new_distinct "
                        "unique=%s raw=%s"
                    ),
                    page_number,
                    candidate_selection.consolidated_count,
                    len(
                        raw_candidates,
                    ),
                )

                break

            if mode == "bulk":
                logger.info(
                    (
                        "normative_discovery_stop "
                        "page=%s reason=bulk_complete "
                        "unique=%s raw=%s"
                    ),
                    page_number,
                    candidate_selection.consolidated_count,
                    len(
                        raw_candidates,
                    ),
                )

                break

            if unique_ratio >= _NORMATIVE_DENSE_UNIQUE_RATIO:
                mode = "bulk"

                logger.info(
                    (
                        "normative_discovery_expand "
                        "page=%s reason=dense_probe "
                        "unique_ratio=%.3f "
                        "unique=%s"
                    ),
                    page_number,
                    unique_ratio,
                    candidate_selection.consolidated_count,
                )

                continue

            if probe_round >= _NORMATIVE_MAX_PROBE_ROUNDS:
                logger.info(
                    (
                        "normative_discovery_stop "
                        "page=%s reason=duplicate_saturation "
                        "probe_rounds=%s "
                        "unique=%s raw=%s"
                    ),
                    page_number,
                    probe_round,
                    candidate_selection.consolidated_count,
                    len(
                        raw_candidates,
                    ),
                )

                break

        return (
            summary,
            candidate_selection,
            _combine_generation_metrics(
                all_metrics,
            ),
        )

    async def execute(
        self,
        *,
        page_number: int,
        extracted_text: str,
        page_facts: PageFacts,
        normative_sources: tuple[
            NormativeSource,
            ...,
        ],
        image_bytes: bytes,
        normative_system_prompt: str | None = None,
        technical_assignment_sources: tuple[
            TechnicalAssignmentSource,
            ...,
        ] = (),
        conflict_candidates: tuple[
            TechnicalAssignmentConflictCandidate,
            ...,
        ] = (),
        user_package_sources: tuple[
            UserPackageSource,
            ...,
        ] = (),
    ) -> tuple[
        str,
        tuple[
            FindingDraft,
            ...,
        ],
        GenerationMetrics,
    ]:
        """Проверяет сам лист и сопоставляет его с N/T/U evidence."""
        normative_source_ids = tuple(
            source.source_id for source in normative_sources if source.source_id
        )

        technical_assignment_source_ids = tuple(
            source.source_id
            for source in technical_assignment_sources
            if source.source_id
        )

        user_package_source_ids = tuple(
            source.source_id for source in user_package_sources if source.source_id
        )

        prompt = build_normative_check_prompt(
            page_number=page_number,
            extracted_text=extracted_text,
            page_facts=page_facts,
            normative_sources=normative_sources,
            technical_assignment_sources=(technical_assignment_sources),
            conflict_candidates=conflict_candidates,
            user_package_sources=user_package_sources,
            normative_text_limit=(self.normative_text_limit),
            normative_system_prompt=(normative_system_prompt),
        )

        (
            summary,
            candidate_selection,
            metrics,
        ) = await self._discover_candidates(
            page_number=page_number,
            prompt=prompt,
            normative_source_ids=normative_source_ids,
            technical_assignment_source_ids=(technical_assignment_source_ids),
            user_package_source_ids=(user_package_source_ids),
            image_bytes=image_bytes,
        )

        source_by_id = {source.source_id: source for source in normative_sources}

        technical_assignment_by_id = {
            source.source_id: source for source in technical_assignment_sources
        }

        user_package_by_id = {
            source.source_id: source for source in user_package_sources
        }

        provenance_lossless = candidate_selection.represented_count == (
            candidate_selection.generated_count - candidate_selection.rejected_count
        )

        logger.info(
            (
                "normative_candidate_selection "
                "page=%s generated=%s "
                "consolidated=%s "
                "duplicates=%s represented=%s "
                "rejected=%s "
                "rejection_reasons=%s "
                "provenance_lossless=%s"
            ),
            page_number,
            candidate_selection.generated_count,
            candidate_selection.consolidated_count,
            candidate_selection.duplicate_count,
            candidate_selection.represented_count,
            candidate_selection.rejected_count,
            candidate_selection.rejection_counts,
            provenance_lossless,
        )

        findings: list[FindingDraft,] = []

        for (
            violation,
            source_indexes,
        ) in zip(
            candidate_selection.candidates,
            candidate_selection.source_indexes_by_candidate,
            strict=True,
        ):
            representative_index = source_indexes[0]

            finding_id = f"p{page_number}-f{representative_index}"

            if (
                len(
                    source_indexes,
                )
                > 1
            ):
                logger.info(
                    (
                        "normative_candidate_"
                        "exact_duplicates_"
                        "consolidated "
                        "page=%s finding_id=%s "
                        "raw_candidate_indexes=%s "
                        "raw_candidate_count=%s"
                    ),
                    page_number,
                    finding_id,
                    source_indexes,
                    len(
                        source_indexes,
                    ),
                )

            requested_normative_ids = string_tuple(
                violation.get(
                    "normative_source_ids",
                ),
                limit=3,
            )

            requested_technical_assignment_ids = string_tuple(
                violation.get(
                    "technical_assignment_source_ids",
                ),
                limit=3,
            )

            requested_user_package_ids = string_tuple(
                violation.get(
                    "user_package_source_ids",
                ),
                limit=3,
            )

            selected_normative_sources = tuple(
                source_by_id[source_id]
                for source_id in requested_normative_ids
                if source_id in source_by_id
            )

            selected_technical_assignment_sources = tuple(
                technical_assignment_by_id[source_id]
                for source_id in requested_technical_assignment_ids
                if source_id in technical_assignment_by_id
            )

            selected_user_package_sources = tuple(
                user_package_by_id[source_id]
                for source_id in requested_user_package_ids
                if source_id in user_package_by_id
            )

            detached_normative_ids = tuple(
                source_id
                for source_id in requested_normative_ids
                if source_id not in source_by_id
            )

            detached_technical_assignment_ids = tuple(
                source_id
                for source_id in requested_technical_assignment_ids
                if source_id not in technical_assignment_by_id
            )

            detached_user_package_ids = tuple(
                source_id
                for source_id in requested_user_package_ids
                if source_id not in user_package_by_id
            )

            if (
                detached_normative_ids
                or detached_technical_assignment_ids
                or detached_user_package_ids
            ):
                logger.info(
                    (
                        "normative_candidate_"
                        "source_ids_detached "
                        "page=%s finding_id=%s "
                        "raw_candidate_indexes=%s "
                        "normative=%s "
                        "technical_assignment=%s "
                        "user_package=%s"
                    ),
                    page_number,
                    finding_id,
                    source_indexes,
                    detached_normative_ids,
                    detached_technical_assignment_ids,
                    detached_user_package_ids,
                )

            selected_any_source = bool(
                selected_normative_sources
                or selected_technical_assignment_sources
                or selected_user_package_sources
            )

            comment = str(
                violation.get(
                    "comment",
                    "",
                )
            ).strip()

            evidence = str(
                violation.get(
                    "evidence",
                    "",
                )
            ).strip()

            recommendation_draft = str(
                violation.get(
                    "recommendation_draft",
                    "",
                )
            ).strip()

            finding_category = category(
                violation.get(
                    "category",
                )
            )

            if (
                not selected_normative_sources
                and (
                    selected_technical_assignment_sources
                    or selected_user_package_sources
                )
                and finding_category == "normative_control"
            ):
                finding_category = "customer_requirements"

            if not selected_any_source and finding_category in {
                "normative_control",
                "customer_requirements",
            }:
                finding_category = "other"

            normalized_status = finding_status(
                violation.get(
                    "status",
                )
            )

            if not selected_any_source:
                normalized_status = "needs_review"

            findings.append(
                FindingDraft(
                    finding_id=finding_id,
                    page=page_number,
                    page_type=page_facts.page_type,
                    category=finding_category,
                    severity=severity(
                        violation.get(
                            "severity",
                        )
                    ),
                    status=normalized_status,
                    comment=comment,
                    evidence=evidence,
                    recommendation_draft=(recommendation_draft),
                    confidence=confidence(
                        violation.get(
                            "confidence",
                        )
                    ),
                    normative_source_ids=tuple(
                        source.source_id for source in selected_normative_sources
                    ),
                    basis=build_basis(
                        selected_normative_sources,
                    ),
                    basis_sources=(selected_normative_sources),
                    experience_query=(
                        build_experience_query(
                            category=finding_category,
                            comment=comment,
                            evidence=evidence,
                            recommendation_draft=(recommendation_draft),
                        )
                    ),
                    technical_assignment_source_ids=tuple(
                        source.source_id
                        for source in selected_technical_assignment_sources
                    ),
                    technical_assignment_basis_sources=(
                        selected_technical_assignment_sources
                    ),
                    user_package_source_ids=tuple(
                        source.source_id for source in selected_user_package_sources
                    ),
                    user_package_basis_sources=(selected_user_package_sources),
                    visual_regions=(
                        parse_finding_visual_regions(
                            violation.get(
                                "visual_regions",
                            )
                        )
                    ),
                )
            )

        logger.info(
            (
                "normative_findings_consolidated "
                "page=%s raw_candidates=%s "
                "findings=%s "
                "duplicates=%s represented=%s "
                "provenance_lossless=%s"
            ),
            page_number,
            candidate_selection.generated_count,
            len(
                findings,
            ),
            candidate_selection.duplicate_count,
            candidate_selection.represented_count,
            provenance_lossless,
        )

        return (
            summary,
            tuple(
                findings,
            ),
            metrics,
        )
