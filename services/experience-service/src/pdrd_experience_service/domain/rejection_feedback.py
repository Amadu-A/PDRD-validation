# services/experience-service/src/pdrd_experience_service/domain/rejection_feedback.py

"""Причины отклонения замечаний и ограниченный комментарий для будущего обучения."""

REASON_CATEGORIES = frozenset(
    {
        "false_positive",
        "wrong_location",
        "wrong_normative_basis",
        "duplicate",
        "misunderstood_drawing",
        "not_applicable",
        "other",
    }
)
MAX_COMMENT_LENGTH = 2000


def validate_feedback(
    *,
    rejected: bool,
    reason_category: str | None,
    comment: str,
    require_reason: bool = False,
) -> tuple[str | None, str]:
    """Проверяет новые решения, сохраняя старые отклонения без придуманной причины."""
    if not isinstance(comment, str) or len(comment) > MAX_COMMENT_LENGTH:
        raise ValueError("Комментарий должен быть строкой до 2000 символов.")
    comment = comment.strip()
    if not rejected:
        if reason_category is not None or comment:
            raise ValueError(
                "Причина и комментарий доступны только для отклонённого замечания."
            )
        return None, ""
    if reason_category is None:
        if require_reason or comment:
            raise ValueError("Выберите причину отклонения замечания.")
        return None, ""
    if not isinstance(reason_category, str) or reason_category not in REASON_CATEGORIES:
        raise ValueError("Неизвестная категория причины отклонения.")
    return reason_category, comment
