# services/analysis-service/tests/unit/test_normative_adaptive_discovery.py

"""Unit tests adaptive normative candidate discovery."""

from typing import Any

from pdrd_analysis_service.application.use_cases.normative import (
    CheckPageAgainstNorms,
)
from pdrd_analysis_service.domain.analysis import (
    GenerationMetrics,
    GenerationResult,
    PageFacts,
)


def _metrics(
    *,
    requested_num_predict: int,
) -> GenerationMetrics:
    """Возвращает deterministic metrics одного fake VLM call."""
    return GenerationMetrics(
        attempt=1,
        done_reason="stop",
        requested_num_predict=(requested_num_predict),
        total_duration_ms=10.0,
        load_duration_ms=1.0,
        prompt_eval_count=100,
        eval_count=50,
        content_length=500,
        thinking_length=0,
    )


def _candidate(
    index: int,
) -> dict[
    str,
    Any,
]:
    """Строит distinct source-less engineering candidate."""
    return {
        "category": "marking",
        "severity": "warning",
        "status": "needs_review",
        "comment": (f"Замечание {index}."),
        "evidence": (f"Наблюдаемый факт {index}."),
        "recommendation_draft": "",
        "confidence": 0.8,
        "normative_source_ids": [],
        "technical_assignment_source_ids": [],
        "user_package_source_ids": [],
    }


def _duplicate_candidate() -> dict[
    str,
    Any,
]:
    """Возвращает один и тот же candidate для saturation scenario."""
    return {
        "category": "marking",
        "severity": "warning",
        "status": "needs_review",
        "comment": ("Отсутствует маркировка."),
        "evidence": ("Один конкретный элемент не имеет маркировки."),
        "recommendation_draft": "",
        "confidence": 0.8,
        "normative_source_ids": [],
        "technical_assignment_source_ids": [],
        "user_package_source_ids": [],
    }


class SequentialVisionModel:
    """Возвращает заранее заданные VLM batches."""

    def __init__(
        self,
        responses: list[
            dict[
                str,
                Any,
            ]
        ],
    ) -> None:
        """Сохраняет fake responses и параметры вызовов."""
        self._responses = list(
            responses,
        )

        self.calls: list[
            dict[
                str,
                Any,
            ]
        ] = []

    async def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None = None,
    ) -> GenerationResult:
        """Возвращает очередной fake batch."""
        self.calls.append(
            {
                "prompt": prompt,
                "schema": schema,
                "num_predict": (num_predict),
                "seed": seed,
                "stage": stage,
                "image_bytes": image_bytes,
            }
        )

        if not self._responses:
            raise AssertionError(
                "Adaptive discovery сделал лишний VLM call.",
            )

        return GenerationResult(
            payload=self._responses.pop(
                0,
            ),
            metrics=_metrics(
                requested_num_predict=(num_predict),
            ),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True


def _page_facts() -> PageFacts:
    """Возвращает минимальный context листа."""
    return PageFacts(
        discipline="ЭОМ",
        page_type="scheme",
        summary=("Принципиальная электрическая схема."),
        objects=("Щит",),
        connections=(),
        labels=(),
        normative_queries=(),
    )


def _use_case(
    model: SequentialVisionModel,
) -> CheckPageAgainstNorms:
    """Строит use case с production-like MAX_ISSUES."""
    return CheckPageAgainstNorms(
        vision_model=model,
        num_predict=14000,
        max_issues=50,
        normative_text_limit=700,
    )


async def test_empty_first_probe_stops_without_confirmation() -> None:
    """Пустой первый probe не создаёт бессмысленный повторный VLM call."""
    model = SequentialVisionModel(
        [
            {
                "summary": "Замечаний нет.",
                "violations": [],
            },
        ]
    )

    summary, findings, metrics = await _use_case(
        model,
    ).execute(
        page_number=1,
        extracted_text=("Тестовый лист."),
        page_facts=_page_facts(),
        normative_sources=(),
        image_bytes=b"png",
    )

    assert summary == "Замечаний нет."

    assert findings == ()

    assert (
        len(
            model.calls,
        )
        == 1
    )

    assert model.calls[0]["stage"] == "normative_check:1:probe1"

    assert metrics.requested_num_predict == 4000


async def test_sparse_first_probe_requires_confirmation_before_stop() -> None:
    """Неполный первый batch подтверждается вторым probe перед остановкой."""
    model = SequentialVisionModel(
        [
            {
                "summary": "Одно замечание.",
                "violations": [
                    _candidate(
                        1,
                    ),
                ],
            },
            {
                "summary": "Новых замечаний нет.",
                "violations": [],
            },
        ]
    )

    summary, findings, metrics = await _use_case(
        model,
    ).execute(
        page_number=2,
        extracted_text=("Тестовый лист."),
        page_facts=_page_facts(),
        normative_sources=(),
        image_bytes=b"png",
    )

    assert summary == "Одно замечание."

    assert (
        len(
            findings,
        )
        == 1
    )

    assert [call["stage"] for call in model.calls] == [
        "normative_check:2:probe1",
        "normative_check:2:probe2",
    ]

    assert all(call["num_predict"] == 4000 for call in model.calls)

    assert metrics.requested_num_predict == 8000

    confirmation_prompt = str(
        model.calls[1]["prompt"],
    )

    assert "Замечание 1." in confirmation_prompt
    assert "Наблюдаемый факт 1." in confirmation_prompt


async def test_sparse_confirmation_recovers_additional_findings() -> None:
    """Первый sparse probe не должен фиксировать случайно малый итог листа."""
    model = SequentialVisionModel(
        [
            {
                "summary": "Первичный sparse batch.",
                "violations": [
                    _candidate(
                        1,
                    ),
                    _candidate(
                        2,
                    ),
                ],
            },
            {
                "summary": "Найдены дополнительные findings.",
                "violations": [
                    _candidate(
                        index,
                    )
                    for index in range(
                        3,
                        11,
                    )
                ],
            },
            {
                "summary": "Новых findings больше нет.",
                "violations": [],
            },
        ]
    )

    _, findings, metrics = await _use_case(
        model,
    ).execute(
        page_number=22,
        extracted_text=("Тестовый лист."),
        page_facts=_page_facts(),
        normative_sources=(),
        image_bytes=b"png",
    )

    assert (
        len(
            findings,
        )
        == 10
    )

    assert [call["stage"] for call in model.calls] == [
        "normative_check:22:probe1",
        "normative_check:22:probe2",
        "normative_check:22:probe3",
    ]

    assert metrics.requested_num_predict == 12000


async def test_duplicate_saturation_requests_distinct_continuation() -> None:
    """Десять дублей не расходуют оставшиеся сорок slots."""
    repeated = [
        _duplicate_candidate()
        for _ in range(
            10,
        )
    ]

    second = _candidate(
        2,
    )

    model = SequentialVisionModel(
        [
            {
                "summary": ("Первый batch."),
                "violations": repeated,
            },
            {
                "summary": ("Continuation."),
                "violations": [
                    second,
                ],
            },
            {
                "summary": ("Новых findings нет."),
                "violations": [],
            },
        ]
    )

    _, findings, metrics = await _use_case(
        model,
    ).execute(
        page_number=3,
        extracted_text=("Тестовый лист."),
        page_facts=_page_facts(),
        normative_sources=(),
        image_bytes=b"png",
    )

    assert (
        len(
            model.calls,
        )
        == 3
    )

    assert (
        len(
            findings,
        )
        == 2
    )

    assert model.calls[0]["stage"] == "normative_check:3:probe1"

    assert model.calls[1]["stage"] == "normative_check:3:probe2"

    assert model.calls[2]["stage"] == "normative_check:3:probe3"

    assert all(call["num_predict"] == 4000 for call in model.calls)

    continuation_prompt = str(model.calls[1]["prompt"])

    assert "ALREADY FOUND DISTINCT CANDIDATES" in continuation_prompt

    assert "Уже найденные distinct candidates" in continuation_prompt

    assert "Отсутствует маркировка." in continuation_prompt

    assert "Один конкретный элемент не имеет маркировки." in continuation_prompt

    assert "Не повторяй их дословно." in continuation_prompt

    assert "Не перефразируй их как новые findings." in continuation_prompt

    assert metrics.requested_num_predict == 12000

    assert metrics.total_duration_ms == 30.0


async def test_dense_discovery_requires_two_confirming_probes_before_bulk() -> None:
    """Один плотный probe не должен сразу открывать большой bulk batch."""
    first_batch = [
        _candidate(
            index,
        )
        for index in range(
            1,
            11,
        )
    ]

    second_batch = [
        _candidate(
            index,
        )
        for index in range(
            11,
            21,
        )
    ]

    model = SequentialVisionModel(
        [
            {
                "summary": ("Первый плотный probe."),
                "violations": first_batch,
            },
            {
                "summary": ("Второй плотный probe."),
                "violations": second_batch,
            },
            {
                "summary": ("Остаток."),
                "violations": [
                    _candidate(
                        21,
                    ),
                    _candidate(
                        22,
                    ),
                ],
            },
        ]
    )

    _, findings, metrics = await _use_case(
        model,
    ).execute(
        page_number=7,
        extracted_text=("Тестовый лист."),
        page_facts=_page_facts(),
        normative_sources=(),
        image_bytes=b"png",
    )

    assert (
        len(
            model.calls,
        )
        == 3
    )

    assert (
        len(
            findings,
        )
        == 22
    )

    first_call = model.calls[0]

    second_call = model.calls[1]

    bulk_call = model.calls[2]

    assert first_call["schema"]["properties"]["violations"]["maxItems"] == 10

    assert second_call["schema"]["properties"]["violations"]["maxItems"] == 10

    assert bulk_call["schema"]["properties"]["violations"]["maxItems"] == 30

    assert first_call["num_predict"] == 4000

    assert second_call["num_predict"] == 4000

    assert bulk_call["num_predict"] == 14000

    assert [call["stage"] for call in model.calls] == [
        "normative_check:7:probe1",
        "normative_check:7:probe2",
        "normative_check:7:bulk",
    ]

    assert "Замечание 1." in str(second_call["prompt"])
    assert "Замечание 20." in str(bulk_call["prompt"])

    assert metrics.requested_num_predict == 22000

    assert metrics.total_duration_ms == 30.0


async def test_repeated_saturation_stops_after_three_probes() -> None:
    """Зацикливание VLM ограничено bounded recovery probes."""
    model = SequentialVisionModel(
        [
            {
                "summary": ("Повторы."),
                "violations": [
                    _duplicate_candidate()
                    for _ in range(
                        10,
                    )
                ],
            },
            {
                "summary": ("Повторы."),
                "violations": [
                    _candidate(
                        2,
                    )
                    for _ in range(
                        10,
                    )
                ],
            },
            {
                "summary": ("Повторы."),
                "violations": [
                    _candidate(
                        3,
                    )
                    for _ in range(
                        10,
                    )
                ],
            },
        ]
    )

    _, findings, metrics = await _use_case(
        model,
    ).execute(
        page_number=9,
        extracted_text=("Тестовый лист."),
        page_facts=_page_facts(),
        normative_sources=(),
        image_bytes=b"png",
    )

    assert (
        len(
            model.calls,
        )
        == 3
    )

    assert (
        len(
            findings,
        )
        == 3
    )

    assert [call["stage"] for call in model.calls] == [
        "normative_check:9:probe1",
        "normative_check:9:probe2",
        "normative_check:9:probe3",
    ]

    assert metrics.requested_num_predict == 12000


async def test_discovery_prompt_forbids_unproven_visual_semantics() -> None:
    """Source-less visual finding не может зависеть от скрытой трактовки символа."""
    model = SequentialVisionModel(
        [
            {
                "summary": ("Замечаний нет."),
                "violations": [],
            },
        ]
    )

    await _use_case(
        model,
    ).execute(
        page_number=23,
        extracted_text=("Два резервуара 100 м3."),
        page_facts=_page_facts(),
        normative_sources=(),
        image_bytes=b"png",
    )

    prompt = str(
        model.calls[0]["prompt"],
    )

    assert "SEMANTIC EVIDENCE DISCIPLINE" in prompt

    assert "сами по себе НЕ доказывают скрытую инженерную классификацию" in prompt

    assert "наземным или подземным" in prompt

    assert "рабочим или резервным" in prompt

    assert "несколько правдоподобных" in prompt

    assert "один и тот же объект/класс объектов" in prompt

    assert "одно и то же свойство" in prompt

    assert "один и тот же смысловой scope" in prompt
