# services/api-gateway/src/pdrd_api_gateway/application/normative_coverage.py

"""Публичная сводка независимой нормативной проверки из сохранённых stage metrics."""


def normative_coverage(result):
    """Нулевые findings и успешный fallback не превращают отсутствующий корпус в полный N-first."""
    scopes = []

    def collect(value):
        """Обходит только структуру результата и метрик, не исследуя тексты и доказательства."""
        if isinstance(value, dict):
            independent = value.get("independent_normative")
            if isinstance(independent, dict):
                scopes.append(independent.get("requirement_plan", independent))
            for key in ("pages", "page", "metrics", "normative_check", "finalization"):
                if key in value:
                    collect(value[key])
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(result)
    if not scopes:
        return None
    complete = all(
        scope.get("coverage", {}).get("source_complete") is True for scope in scopes
    )
    available = complete and all(scope.get("generations") for scope in scopes)
    return {
        "normative_coverage": "complete" if complete else "incomplete",
        "normative_atomic_check": "available" if available else "unavailable",
        "corpus_requirements": max(
            (
                scope.get("coverage", {}).get("corpus_requirements", 0)
                for scope in scopes
            ),
            default=0,
        ),
        "unavailable_documents": [
            row for scope in scopes for row in scope.get("unavailable_documents", [])
        ],
        "coverage_limitations": [
            row for scope in scopes for row in scope.get("coverage_limitations", [])
        ],
        "normative_evaluation_counts": {
            key: max(
                (scope.get("evaluation_counts", {}).get(key, 0) for scope in scopes),
                default=0,
            )
            for key in {
                key for scope in scopes for key in scope.get("evaluation_counts", {})
            }
        },
        "normative_coverage_message": (
            "Атомарный корпус получен. Поддержка правил и нормативный статус оцениваются отдельно."
        )
        if complete
        else (
            "Нормативная проверка неполная: атомарный корпус отсутствует или содержит непрочитанные страницы. "
            "Выполненные проверки по доступным источникам сохранены."
        ),
    }
