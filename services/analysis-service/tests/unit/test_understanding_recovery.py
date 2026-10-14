# services/analysis-service/tests/unit/test_understanding_recovery.py

"""Восстановление Understanding сохраняет D/EQ, лимиты, отмену и исходный контекст."""

from dataclasses import replace

import pytest
from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelError,
    VisionModelOutputBudgetError,
)
from pdrd_analysis_service.application.understanding_recovery import (
    recover_page_understanding,
)
from pdrd_analysis_service.application.use_cases.understanding import UnderstandPage
from pdrd_analysis_service.domain.analysis import GenerationMetrics, GenerationResult
from pdrd_analysis_service.domain.equipment_specification import SpecificationCancelled


def metrics(reason="stop"):
    """Возвращает измеримые затраты каждого вызова, включая неудачные."""
    return GenerationMetrics(1, reason, 3200, 10, 1, 100, 200, 300, 0)


class Model:
    """Проверяет контракт схемы и отдаёт последовательные атомарные записи."""

    def __init__(self, overflow_capacity=None, light_overflow=False):
        """Настраивает границу переполнения для уменьшения порций."""
        self.calls = []
        self.offsets = {"document_facts": 0, "equipment_identities": 0}
        self.overflow_capacity = overflow_capacity
        self.light_overflow = light_overflow

    async def generate_json(self, **kwargs):
        """Неполный ответ никогда не выдаётся за подтверждённые факты."""
        self.calls.append(kwargs)
        fields = kwargs["schema"]["properties"]
        if ":recovery:" not in kwargs["stage"] or (
            self.light_overflow and len(fields) == 7
        ):
            raise VisionModelOutputBudgetError(
                "Переполнение", metrics=metrics("length")
            )
        payload = {}
        for name, schema in fields.items():
            if name in self.offsets:
                capacity = schema["maxItems"]
                if (
                    self.overflow_capacity is not None
                    and capacity > self.overflow_capacity
                ):
                    raise VisionModelOutputBudgetError(
                        "Переполнение порции", metrics=metrics("length")
                    )
                start = self.offsets[name]
                self.offsets[name] += capacity
                if name == "document_facts":
                    payload[name] = [
                        {
                            "kind": "equipment",
                            "subject_name": f"Узел {i}",
                            "evidence_text": f"Модель М-{i}",
                        }
                        for i in range(start, start + capacity)
                    ]
                else:
                    payload[name] = [
                        {
                            "manufacturer": "",
                            "model": f"М-{i}",
                            "status": "needs_review",
                            "evidence_text": f"Модель М-{i}",
                        }
                        for i in range(start, start + capacity)
                    ]
            else:
                payload[name] = [] if schema.get("type") == "array" else "Схема"
        return GenerationResult(payload=payload, metrics=metrics())


@pytest.mark.parametrize(
    "d,eq", [(True, True), (True, False), (False, True), (False, False)]
)
async def test_budget_recovery_preserves_independent_features_and_metrics(d, eq):
    """Прежние лимиты 12/40 достигаются порциями без потери отдельных моделей."""
    model = Model()
    facts, total = await UnderstandPage(model, 3200).execute(
        page_number=20,
        heuristic_page_type="scheme",
        image_bytes=b"same-image",
        extracted_text="\n".join(f"Модель М-{i}" for i in range(40)),
        use_document_context=d,
        use_equipment_web_search=eq,
    )
    assert len(facts.document_facts) == (12 if d else 0)
    assert len(facts.equipment_identities) == (40 if eq else 0)
    assert all(call["image_bytes"] == b"same-image" for call in model.calls)
    assert total.eval_count == 200 * len(model.calls)
    assert total.requested_num_predict == 3200 * len(model.calls)
    assert all(call["num_predict"] == 3200 for call in model.calls)
    if not d and not eq:
        assert "core-fields" in model.calls[1]["stage"]


async def test_adaptive_sizes_and_explicit_failure_for_single_record():
    """Не повторяет одинаковый переполненный запрос; минимум — одна запись."""
    model = Model(overflow_capacity=1)
    payload, _ = await recover_page_understanding(
        model,
        page_number=20,
        heuristic_page_type="scheme",
        extracted_text="М-1",
        image_bytes=b"image",
        num_predict=3200,
        max_facts=4,
        max_equipment=0,
        initial_error=VisionModelOutputBudgetError("bulk"),
    )
    sizes = [
        call["schema"]["properties"]["document_facts"]["maxItems"]
        for call in model.calls
        if "document_facts" in call["schema"]["properties"]
    ]
    assert sizes[:3] == [4, 2, 1]
    assert len(payload["document_facts"]) == 4
    with pytest.raises(VisionModelOutputBudgetError):
        await recover_page_understanding(
            Model(overflow_capacity=0),
            page_number=20,
            heuristic_page_type="scheme",
            extracted_text="",
            image_bytes=b"image",
            num_predict=3200,
            max_facts=4,
            max_equipment=0,
            initial_error=VisionModelOutputBudgetError("bulk"),
        )


async def test_core_overflow_splits_fields_and_cancel_stops_before_gpu():
    """Облегчённое ядро делится по полям; отмена не запускает очередное обращение."""
    model = Model(light_overflow=True)
    payload, _ = await recover_page_understanding(
        model,
        page_number=20,
        heuristic_page_type="scheme",
        extracted_text="",
        image_bytes=b"image",
        num_predict=3200,
        max_facts=1,
        max_equipment=0,
        initial_error=VisionModelOutputBudgetError("bulk"),
    )
    assert payload["summary"] == "Схема" and len(payload["document_facts"]) == 1

    async def cancelled():
        """Имитирует подтверждённую отмену задания."""
        return True

    fresh = Model()
    with pytest.raises(SpecificationCancelled):
        await recover_page_understanding(
            fresh,
            page_number=20,
            heuristic_page_type="scheme",
            extracted_text="",
            image_bytes=b"image",
            num_predict=3200,
            max_facts=1,
            max_equipment=0,
            initial_error=VisionModelOutputBudgetError("bulk"),
            should_stop=cancelled,
        )
    assert not fresh.calls


async def test_network_failure_is_not_retried_as_budget_recovery():
    """Сетевая ошибка не маскируется дополнительными вызовами."""

    class Broken:
        """Модель с отказом сети до получения ответа."""

        async def generate_json(self, **kwargs):
            """Сохраняет тип исходной ошибки."""
            raise VisionModelError("Сеть недоступна")

    with pytest.raises(VisionModelError, match="Сеть недоступна"):
        await UnderstandPage(Broken(), 3200).execute(
            page_number=20,
            heuristic_page_type="scheme",
            extracted_text="",
            image_bytes=b"image",
        )


async def test_completed_json_with_length_is_also_recovered():
    """Валидный JSON с finish_reason=length не завершает проверку молча."""

    class LengthModel(Model):
        """Первый синтаксически полный ответ всё же достиг предела провайдера."""

        async def generate_json(self, **kwargs):
            """После первого ответа использует обычное раздельное восстановление."""
            if not self.calls:
                self.calls.append(kwargs)
                return GenerationResult(
                    {"summary": "Обрезанный смысл"},
                    replace(metrics(), done_reason="length"),
                )
            return await super().generate_json(**kwargs)

    facts, _ = await UnderstandPage(LengthModel(), 3200).execute(
        page_number=20,
        heuristic_page_type="scheme",
        extracted_text="",
        image_bytes=b"image",
    )
    assert facts.summary == "Схема" and len(facts.document_facts) == 12
