# services/analysis-service/src/pdrd_analysis_service/application/project_context_prompt.py

"""Prompt классификации выбранного диапазона ПЗ."""

import json

from pdrd_analysis_service.domain.project_context import (
    ProjectContextPage,
)


def build_project_context_classification_prompt(
    pages: tuple[
        ProjectContextPage,
        ...,
    ],
) -> str:
    """Формирует prompt без поиска проектных ошибок."""
    payload = [
        {
            "page": page.page_number,
            "text": page.text[:2200],
        }
        for page in pages
    ]

    return f"""
Проверь страницы, которые пользователь указал как диапазон
ПОЯСНИТЕЛЬНОЙ ЗАПИСКИ проектной/рабочей документации.

Данные:
{json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}

Для каждой страницы выбери ровно один kind:

- explanatory_note:
  связный текст ПЗ, общие положения, описание проектных решений,
  технические требования, описание оборудования/систем,
  текстовые разделы с допустимыми небольшими таблицами;

- drawing:
  основное содержание страницы — схема, план, чертёж,
  графическое расположение элементов;

- specification:
  основное содержание — спецификация оборудования, изделий,
  материалов, кабельный журнал или большая табличная ведомость;

- other:
  титульный лист, пустая страница или материал,
  который нельзя уверенно считать ПЗ.

Классифицируй только по переданному тексту.
Не ищи ошибки проекта.
Не придумывай отсутствующий текст.
Верни только JSON.
""".strip()
