# services/knowledge-service/src/pdrd_knowledge_service/application/normative_requirement_policy.py

"""Ограничения очереди атомарного каталога, общие для HTTP, CLI и dispatcher."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NormativeRequirementPolicy:
    """Новые загрузки сохраняют намерение, но не обходят общий admission limit."""

    max_in_flight: int = 2
    max_attempts: int = 3
    stale_seconds: int = 600
    retry_backoff_seconds: int = 60

    def __post_init__(self):
        """Ошибочная конфигурация не запускает неограниченную очередь."""
        if not 1 <= self.max_in_flight <= 32 or not 1 <= self.max_attempts <= 20:
            raise ValueError("Недопустимые ограничения атомарной очереди.")
        if self.stale_seconds < 1 or self.retry_backoff_seconds < 0:
            raise ValueError("Недопустимые интервалы восстановления.")
