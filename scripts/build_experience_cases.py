# scripts/build_experience_cases.py

"""Подготовка Базы Опыта из PDF до и после исправлений.

Скрипт автоматически перебирает все проекты в:

    data/knowledge/experience/cases/

Для каждого неподготовленного проекта он:

1. Находит ровно один PDF в ``before`` и один PDF в ``after``.
2. Извлекает замечания из BEFORE:
   - в первую очередь из текстовых PDF-аннотаций;
   - если их нет во всём документе, использует красный текст как fallback.
3. Даёт пользователю исключить ошибочно найденные страницы.
4. Даёт пользователю исключить ошибочно найденные замечания на каждой странице.
5. Показывает итоговый подтверждённый список.
6. Просит сопоставить страницы BEFORE с исправленными страницами AFTER.
7. Создаёт:
   - annotations/issues.json
   - annotations/meta.json

Если в папке ``annotations`` уже есть хотя бы один файл, проект полностью
пропускается. Чтобы подготовить его заново, удалите файлы из ``annotations``
и снова запустите скрипт.

Запуск из корня репозитория:

    python -m scripts.build_experience_cases
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import fitz


@dataclass
class Issue:
    """Одно замечание-кандидат."""

    page: int
    text: str
    rect: fitz.Rect
    source: str


@dataclass
class ExtractionResult:
    """Результат автоматического обнаружения замечаний."""

    issues: list[Issue]
    page_count: int
    detection_mode: str


def get_repo_root() -> Path:
    """Получить корень репозитория."""

    return Path(__file__).resolve().parent.parent


def get_cases_dir() -> Path:
    """Получить каталог кейсов Базы Опыта."""

    return (
        get_repo_root()
        / "data"
        / "knowledge"
        / "experience"
        / "cases"
    )


def clean_text(text: str) -> str:
    """Нормализовать пробелы без изменения содержания текста."""

    return re.sub(r"\s+", " ", text).strip()


def normalize_text(text: str) -> str:
    """Нормализовать текст для поиска точных дублей."""

    return re.sub(
        r"[^a-zа-яё0-9]+",
        " ",
        text.lower(),
    ).strip()


def file_sha256(path: Path) -> str:
    """Посчитать SHA-256 файла."""

    digest = hashlib.sha256()

    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


def find_projects(cases_dir: Path) -> list[Path]:
    """Найти все каталоги проектов в cases."""

    if not cases_dir.is_dir():
        raise RuntimeError(
            "Каталог Базы Опыта не найден:\n"
            f"{cases_dir}"
        )

    return sorted(
        path
        for path in cases_dir.iterdir()
        if path.is_dir()
        and not path.name.startswith(".")
    )


def annotations_exist(annotations_dir: Path) -> bool:
    """Проверить, подготовлен ли уже проект.

    Если в ``annotations`` уже есть хотя бы один файл, проект не меняем.
    Для повторной подготовки пользователь вручную удаляет эти файлы.
    """

    if not annotations_dir.is_dir():
        return False

    return any(
        path.is_file()
        for path in annotations_dir.iterdir()
    )


def find_single_pdf(
    directory: Path,
    folder_name: str,
) -> Path:
    """Найти единственный PDF в before или after."""

    if not directory.is_dir():
        raise RuntimeError(
            f"Не найдена папка {folder_name}: {directory}"
        )

    pdf_files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".pdf"
    )

    if not pdf_files:
        raise RuntimeError(
            f"В папке {folder_name} нет PDF."
        )

    if len(pdf_files) > 1:
        names = "\n".join(
            f"  - {path.name}"
            for path in pdf_files
        )

        raise RuntimeError(
            f"В папке {folder_name} должен быть ровно один PDF.\n"
            f"Найдены:\n{names}"
        )

    return pdf_files[0]


def rgb_from_int(value: int) -> tuple[int, int, int]:
    """Преобразовать integer-цвет PyMuPDF в RGB."""

    return (
        (value >> 16) & 255,
        (value >> 8) & 255,
        value & 255,
    )


def is_reviewer_red(
    color: tuple[int, int, int],
) -> bool:
    """Определить красный/оранжевый текст как fallback-кандидат."""

    red, green, blue = color

    return (
        red >= 170
        and green <= 130
        and blue <= 130
        and red - green >= 50
        and red - blue >= 50
    )


def extract_annotation_issues_from_page(
    page: fitz.Page,
    page_number: int,
) -> list[Issue]:
    """Получить замечания из текстового содержимого PDF-аннотаций."""

    issues: list[Issue] = []
    annotation = page.first_annot

    while annotation is not None:
        info = annotation.info or {}

        text = clean_text(
            info.get("content", "")
        )

        if text:
            issues.append(
                Issue(
                    page=page_number,
                    text=text,
                    rect=fitz.Rect(
                        annotation.rect
                    ),
                    source=(
                        "pdf_annotation:"
                        f"{annotation.type[1]}"
                    ),
                )
            )

        annotation = annotation.next

    return issues


def extract_all_annotation_issues(
    document: fitz.Document,
) -> list[Issue]:
    """Извлечь текст всех PDF-аннотаций документа."""

    issues: list[Issue] = []

    for page_number, page in enumerate(
        document,
        start=1,
    ):
        issues.extend(
            extract_annotation_issues_from_page(
                page,
                page_number,
            )
        )

    return issues


def extract_red_spans(
    page: fitz.Page,
) -> list[dict]:
    """Получить отдельные красные текстовые фрагменты страницы."""

    result: list[dict] = []
    page_data = page.get_text("dict")

    for block in page_data.get(
        "blocks",
        [],
    ):
        for line in block.get(
            "lines",
            [],
        ):
            for span in line.get(
                "spans",
                [],
            ):
                text = clean_text(
                    span.get(
                        "text",
                        "",
                    )
                )

                if not text:
                    continue

                color = rgb_from_int(
                    int(
                        span.get(
                            "color",
                            0,
                        )
                    )
                )

                if not is_reviewer_red(
                    color
                ):
                    continue

                result.append(
                    {
                        "text": text,
                        "rect": fitz.Rect(
                            span["bbox"]
                        ),
                        "font_size": float(
                            span.get(
                                "size",
                                0.0,
                            )
                        ),
                    }
                )

    return result


def can_join_spans(
    previous: dict,
    current: dict,
) -> bool:
    """Проверить, относятся ли две строки к одному замечанию."""

    previous_rect: fitz.Rect = (
        previous["rect"]
    )

    current_rect: fitz.Rect = (
        current["rect"]
    )

    font_size = max(
        previous["font_size"],
        current["font_size"],
        1.0,
    )

    vertical_gap = (
        current_rect.y0
        - previous_rect.y1
    )

    horizontal_delta = abs(
        current_rect.x0
        - previous_rect.x0
    )

    return (
        -0.5 * font_size
        <= vertical_gap
        <= 0.9 * font_size
        and horizontal_delta
        <= max(
            40.0,
            1.8 * font_size,
        )
    )


def extract_red_text_issues_from_page(
    page: fitz.Page,
    page_number: int,
) -> list[Issue]:
    """Объединить красные текстовые строки в замечания-кандидаты."""

    spans = sorted(
        extract_red_spans(
            page
        ),
        key=lambda item: (
            item["rect"].y0,
            item["rect"].x0,
        ),
    )

    groups: list[list[dict]] = []

    for span in spans:
        target_group: list[dict] | None = None

        for group in reversed(
            groups
        ):
            if can_join_spans(
                group[-1],
                span,
            ):
                target_group = group
                break

        if target_group is None:
            groups.append(
                [span]
            )
        else:
            target_group.append(
                span
            )

    issues: list[Issue] = []

    for group in groups:
        text = clean_text(
            " ".join(
                span["text"]
                for span in group
            )
        )

        if not text:
            continue

        rect = fitz.Rect(
            group[0]["rect"]
        )

        for span in group[1:]:
            rect.include_rect(
                span["rect"]
            )

        issues.append(
            Issue(
                page=page_number,
                text=text,
                rect=rect,
                source="reviewer_colored_text",
            )
        )

    return issues


def remove_exact_duplicates(
    issues: list[Issue],
) -> list[Issue]:
    """Удалить точные текстовые дубли в пределах страницы."""

    result: list[Issue] = []
    seen: set[
        tuple[int, str]
    ] = set()

    for issue in issues:
        key = (
            issue.page,
            normalize_text(
                issue.text
            ),
        )

        if not key[1]:
            continue

        if key in seen:
            continue

        seen.add(key)
        result.append(issue)

    return result


def extract_issues(
    pdf_path: Path,
) -> ExtractionResult:
    """Извлечь замечания максимально надёжным доступным способом.

    Если в документе есть хотя бы одна текстовая PDF-аннотация,
    используем только PDF-аннотации.

    Красный текст в этом случае не используем, потому что на инженерной
    схеме красным могут быть нарисованы обычные обозначения, жилы,
    номера и другие элементы, не являющиеся замечаниями.

    Если текстовых PDF-аннотаций нет вообще, включаем fallback
    по красному тексту и обязательно просим ручное подтверждение.
    """

    with fitz.open(
        pdf_path
    ) as document:
        page_count = len(
            document
        )

        annotation_issues = (
            extract_all_annotation_issues(
                document
            )
        )

        if annotation_issues:
            issues = remove_exact_duplicates(
                annotation_issues
            )

            issues.sort(
                key=lambda issue: (
                    issue.page,
                    issue.rect.y0,
                    issue.rect.x0,
                )
            )

            return ExtractionResult(
                issues=issues,
                page_count=page_count,
                detection_mode="pdf_annotations",
            )

        red_issues: list[Issue] = []

        for page_number, page in enumerate(
            document,
            start=1,
        ):
            red_issues.extend(
                extract_red_text_issues_from_page(
                    page,
                    page_number,
                )
            )

        red_issues = (
            remove_exact_duplicates(
                red_issues
            )
        )

        red_issues.sort(
            key=lambda issue: (
                issue.page,
                issue.rect.y0,
                issue.rect.x0,
            )
        )

        return ExtractionResult(
            issues=red_issues,
            page_count=page_count,
            detection_mode="red_text_fallback",
        )


def parse_number_list(
    raw_value: str,
) -> list[int]:
    """Разобрать строку вида ``1 3 7`` или ``1,3,7``."""

    if not raw_value.strip():
        return []

    values = [
        value
        for value in re.split(
            r"[\s,;]+",
            raw_value.strip(),
        )
        if value
    ]

    try:
        return [
            int(value)
            for value in values
        ]

    except ValueError as error:
        raise ValueError(
            "Допустимы только целые номера."
        ) from error


def group_issues_by_page(
    issues: list[Issue],
) -> dict[int, list[Issue]]:
    """Сгруппировать замечания по страницам."""

    grouped: dict[
        int,
        list[Issue],
    ] = {}

    for issue in issues:
        grouped.setdefault(
            issue.page,
            [],
        ).append(issue)

    return grouped


def confirm_pages(
    issues: list[Issue],
) -> list[Issue]:
    """Дать пользователю исключить ошибочно найденные страницы."""

    grouped = group_issues_by_page(
        issues
    )

    pages = sorted(
        grouped
    )

    print()
    print(
        "Найденные страницы-кандидаты:"
    )

    for page in pages:
        print(
            f"  Страница {page}: "
            f"{len(grouped[page])} "
            "замечаний-кандидатов"
        )

    print()
    print(
        "Проверьте список страниц."
    )

    print(
        "Введите номера страниц, "
        "которые надо ИСКЛЮЧИТЬ."
    )

    print(
        "Enter = оставить все страницы."
    )

    while True:
        raw_value = input(
            "Исключить страницы: "
        )

        try:
            excluded_pages = (
                parse_number_list(
                    raw_value
                )
            )

        except ValueError as error:
            print(
                f"Ошибка: {error}"
            )
            continue

        unknown_pages = [
            page
            for page in excluded_pages
            if page not in pages
        ]

        if unknown_pages:
            print(
                "Этих страниц нет среди найденных: "
                + ", ".join(
                    map(
                        str,
                        unknown_pages,
                    )
                )
            )
            continue

        excluded_set = set(
            excluded_pages
        )

        confirmed = [
            issue
            for issue in issues
            if issue.page
            not in excluded_set
        ]

        kept_pages = sorted(
            {
                issue.page
                for issue in confirmed
            }
        )

        print()

        if kept_pages:
            print(
                "После фильтрации остаются страницы: "
                + ", ".join(
                    map(
                        str,
                        kept_pages,
                    )
                )
            )

        else:
            print(
                "После фильтрации "
                "не осталось страниц."
            )

        answer = input(
            "Подтвердить страницы? [Y/n]: "
        ).strip().lower()

        if answer in {
            "",
            "y",
            "yes",
            "д",
            "да",
        }:
            return confirmed


def confirm_issues_on_page(
    page_number: int,
    issues: list[Issue],
) -> list[Issue]:
    """Подтвердить замечания одной страницы."""

    print()
    print("-" * 78)

    print(
        f"Проверка замечаний "
        f"страницы {page_number}"
    )

    print("-" * 78)

    for index, issue in enumerate(
        issues,
        start=1,
    ):
        print()

        print(
            f"[{index}] {issue.text}"
        )

        print(
            "    Координаты: "
            f"x0={issue.rect.x0:.2f}, "
            f"y0={issue.rect.y0:.2f}, "
            f"x1={issue.rect.x1:.2f}, "
            f"y1={issue.rect.y1:.2f}"
        )

        print(
            f"    Источник: "
            f"{issue.source}"
        )

    print()

    print(
        "Введите номера ЛИШНИХ замечаний, "
        "которые надо исключить."
    )

    print(
        "Например: 2 5"
    )

    print(
        "Enter = все замечания настоящие."
    )

    while True:
        raw_value = input(
            "Исключить замечания: "
        )

        try:
            excluded_numbers = (
                parse_number_list(
                    raw_value
                )
            )

        except ValueError as error:
            print(
                f"Ошибка: {error}"
            )
            continue

        valid_numbers = set(
            range(
                1,
                len(issues) + 1,
            )
        )

        invalid_numbers = [
            number
            for number in excluded_numbers
            if number
            not in valid_numbers
        ]

        if invalid_numbers:
            print(
                "Нет замечаний с номерами: "
                + ", ".join(
                    map(
                        str,
                        invalid_numbers,
                    )
                )
            )
            continue

        excluded_set = set(
            excluded_numbers
        )

        confirmed = [
            issue
            for index, issue in enumerate(
                issues,
                start=1,
            )
            if index
            not in excluded_set
        ]

        print()

        print(
            f"Останется замечаний: "
            f"{len(confirmed)}"
        )

        for index, issue in enumerate(
            confirmed,
            start=1,
        ):
            print(
                f"  {index}. "
                f"{issue.text}"
            )

        answer = input(
            "Подтвердить замечания "
            "этой страницы? [Y/n]: "
        ).strip().lower()

        if answer in {
            "",
            "y",
            "yes",
            "д",
            "да",
        }:
            return confirmed


def confirm_all_issues(
    issues: list[Issue],
) -> list[Issue]:
    """Интерактивно подтвердить замечания всех страниц."""

    grouped = group_issues_by_page(
        issues
    )

    confirmed: list[Issue] = []

    for page_number in sorted(
        grouped
    ):
        confirmed.extend(
            confirm_issues_on_page(
                page_number,
                grouped[
                    page_number
                ],
            )
        )

    confirmed.sort(
        key=lambda issue: (
            issue.page,
            issue.rect.y0,
            issue.rect.x0,
        )
    )

    return confirmed


def show_final_selection(
    issues: list[Issue],
) -> bool:
    """Показать окончательный набор замечаний."""

    print()
    print("=" * 78)

    print(
        "ИТОГОВЫЕ ПОДТВЕРЖДЁННЫЕ ЗАМЕЧАНИЯ"
    )

    print("=" * 78)

    if not issues:
        print(
            "Замечаний не осталось."
        )
        return False

    grouped = group_issues_by_page(
        issues
    )

    total = 0

    for page_number in sorted(
        grouped
    ):
        print()

        print(
            f"Страница {page_number}:"
        )

        for issue in grouped[
            page_number
        ]:
            total += 1

            print(
                f"  {total}. "
                f"{issue.text}"
            )

    print()

    print(
        f"Всего подтверждено: "
        f"{total}"
    )

    answer = input(
        "Список правильный? [Y/n]: "
    ).strip().lower()

    return answer in {
        "",
        "y",
        "yes",
        "д",
        "да",
    }


def parse_after_pages(
    raw_value: str,
    before_pages: list[int],
    after_page_count: int,
) -> dict[int, int]:
    """Разобрать отображение страниц BEFORE -> AFTER."""

    parts = [
        value
        for value in re.split(
            r"[\s,;]+",
            raw_value.strip(),
        )
        if value
    ]

    if len(parts) != len(
        before_pages
    ):
        raise ValueError(
            f"Нужно указать "
            f"{len(before_pages)} страниц, "
            f"а введено {len(parts)}."
        )

    try:
        after_pages = [
            int(value)
            for value in parts
        ]

    except ValueError as error:
        raise ValueError(
            "Допустимы только целые номера страниц."
        ) from error

    invalid_pages = [
        page
        for page in after_pages
        if page < 1
        or page > after_page_count
    ]

    if invalid_pages:
        raise ValueError(
            "В AFTER нет страниц: "
            + ", ".join(
                map(
                    str,
                    invalid_pages,
                )
            )
        )

    return dict(
        zip(
            before_pages,
            after_pages,
            strict=True,
        )
    )


def ask_after_mapping(
    project_name: str,
    before_pages: list[int],
    after_page_count: int,
) -> dict[int, int]:
    """Запросить соответствие страниц BEFORE -> AFTER."""

    pages_text = ", ".join(
        map(
            str,
            before_pages,
        )
    )

    print()
    print("=" * 78)

    print(
        "СОПОСТАВЛЕНИЕ С ИСПРАВЛЕННЫМ ПРОЕКТОМ"
    )

    print("=" * 78)
    print()

    print(
        f"Для проекта {project_name} "
        "подтверждены замечания "
        f"на страницах: {pages_text}."
    )

    print(
        f"Исправленный PDF содержит "
        f"{after_page_count} страниц."
    )

    print()

    print(
        "Укажите соответствующие страницы "
        "исправленного проекта "
        "в том же порядке."
    )

    print(
        f"BEFORE: {pages_text}"
    )

    while True:
        raw_value = input(
            "AFTER : "
        )

        try:
            mapping = parse_after_pages(
                raw_value,
                before_pages,
                after_page_count,
            )

        except ValueError as error:
            print(
                f"Ошибка: {error}"
            )
            continue

        print()

        print(
            "Получено соответствие:"
        )

        for (
            before_page,
            after_page,
        ) in mapping.items():
            print(
                f"  BEFORE {before_page}"
                f" -> AFTER {after_page}"
            )

        answer = input(
            "Подтвердить? [Y/n]: "
        ).strip().lower()

        if answer in {
            "",
            "y",
            "yes",
            "д",
            "да",
        }:
            return mapping


def rect_to_dict(
    rect: fitz.Rect,
) -> dict[str, float]:
    """Сериализовать координаты замечания."""

    return {
        "x0": round(
            rect.x0,
            2,
        ),
        "y0": round(
            rect.y0,
            2,
        ),
        "x1": round(
            rect.x1,
            2,
        ),
        "y1": round(
            rect.y1,
            2,
        ),
    }


def write_json(
    path: Path,
    payload: dict,
) -> None:
    """Записать JSON в UTF-8."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def process_project(
    project_dir: Path,
    project_number: int,
    projects_count: int,
) -> bool:
    """Обработать один проект."""

    project_name = (
        project_dir.name
    )

    print()
    print("=" * 78)

    print(
        f"Проект {project_number}"
        f"/{projects_count}: "
        f"{project_name}"
    )

    print("=" * 78)

    annotations_dir = (
        project_dir
        / "annotations"
    )

    # Готовые кейсы пропускаем сразу.
    # Никакие файлы проекта не читаем и не изменяем.
    if annotations_exist(
        annotations_dir
    ):
        print(
            "[SKIP] Проект уже подготовлен: "
            "в annotations есть файлы."
        )
        return False

    before_pdf = find_single_pdf(
        project_dir / "before",
        "before",
    )

    after_pdf = find_single_pdf(
        project_dir / "after",
        "after",
    )

    # Сохраняем утверждённую структуру,
    # даже если DXF пока отсутствует.
    (
        project_dir
        / "dxf"
        / "before"
    ).mkdir(
        parents=True,
        exist_ok=True,
    )

    (
        project_dir
        / "dxf"
        / "after"
    ).mkdir(
        parents=True,
        exist_ok=True,
    )

    print(
        f"BEFORE: {before_pdf.name}"
    )

    print(
        f"AFTER : {after_pdf.name}"
    )

    extraction = extract_issues(
        before_pdf
    )

    with fitz.open(
        after_pdf
    ) as after_document:
        after_page_count = len(
            after_document
        )

    print(
        f"Страниц BEFORE: "
        f"{extraction.page_count}"
    )

    print(
        f"Страниц AFTER : "
        f"{after_page_count}"
    )

    print()

    if (
        extraction.detection_mode
        == "pdf_annotations"
    ):
        print(
            "Режим обнаружения: "
            "текстовые PDF-аннотации "
            "(высокая надёжность)."
        )

    else:
        print(
            "Режим обнаружения: "
            "красный текст "
            "(fallback, требуется "
            "особенно внимательная проверка)."
        )

    print(
        "Найдено замечаний-кандидатов: "
        f"{len(extraction.issues)}"
    )

    if not extraction.issues:
        print(
            "[WARN] Замечания "
            "автоматически не найдены."
        )
        return False

    page_confirmed = confirm_pages(
        extraction.issues
    )

    if not page_confirmed:
        print(
            "[WARN] Все найденные страницы "
            "были исключены."
        )
        return False

    issue_confirmed = (
        confirm_all_issues(
            page_confirmed
        )
    )

    if not issue_confirmed:
        print(
            "[WARN] Все найденные замечания "
            "были исключены."
        )
        return False

    if not show_final_selection(
        issue_confirmed
    ):
        print(
            "[SKIP] Итоговый список "
            "не подтверждён. "
            "JSON не создаются."
        )
        return False

    reviewed_pages = sorted(
        {
            issue.page
            for issue in issue_confirmed
        }
    )

    mapping = ask_after_mapping(
        project_name,
        reviewed_pages,
        after_page_count,
    )

    page_sizes: dict[
        int,
        dict[str, float],
    ] = {}

    with fitz.open(
        before_pdf
    ) as document:
        for page_number in reviewed_pages:
            rect = document[
                page_number - 1
            ].rect

            page_sizes[
                page_number
            ] = {
                "width": round(
                    rect.width,
                    2,
                ),
                "height": round(
                    rect.height,
                    2,
                ),
            }

    issue_records: list[dict] = []

    for index, issue in enumerate(
        issue_confirmed,
        start=1,
    ):
        issue_records.append(
            {
                "id": (
                    f"issue-{index:03d}"
                ),
                "text": issue.text,
                "category": None,
                "status": (
                    "mapped_to_after"
                ),
                "verified_fixed": False,
                "before": {
                    "pdf_page": (
                        issue.page
                    ),
                    "page_size_points": (
                        page_sizes[
                            issue.page
                        ]
                    ),
                    "bbox_points": (
                        rect_to_dict(
                            issue.rect
                        )
                    ),
                    "source": (
                        issue.source
                    ),
                },
                "after": {
                    "pdf_page": (
                        mapping[
                            issue.page
                        ]
                    ),
                },
            }
        )

    issues_payload = {
        "schema_version": 2,
        "project_id": project_name,
        "before_pdf": (
            before_pdf
            .relative_to(
                project_dir
            )
            .as_posix()
        ),
        "after_pdf": (
            after_pdf
            .relative_to(
                project_dir
            )
            .as_posix()
        ),
        "issues": issue_records,
    }

    meta_payload = {
        "schema_version": 2,
        "project_id": project_name,
        "generated_at": (
            datetime
            .now(
                timezone.utc
            )
            .isoformat()
        ),
        "detection": {
            "mode": (
                extraction.detection_mode
            ),
            "automatic_candidates": len(
                extraction.issues
            ),
            "confirmed_issues": len(
                issue_confirmed
            ),
        },
        "before": {
            "file": (
                before_pdf
                .relative_to(
                    project_dir
                )
                .as_posix()
            ),
            "sha256": file_sha256(
                before_pdf
            ),
            "page_count": (
                extraction.page_count
            ),
        },
        "after": {
            "file": (
                after_pdf
                .relative_to(
                    project_dir
                )
                .as_posix()
            ),
            "sha256": file_sha256(
                after_pdf
            ),
            "page_count": (
                after_page_count
            ),
        },
        "reviewed_before_pages": (
            reviewed_pages
        ),
        "page_mapping": [
            {
                "before_pdf_page": (
                    before_page
                ),
                "after_pdf_page": (
                    after_page
                ),
            }
            for (
                before_page,
                after_page,
            ) in mapping.items()
        ],
        "issues_count": len(
            issue_records
        ),
        "dxf_available": {
            "before": any(
                (
                    project_dir
                    / "dxf"
                    / "before"
                ).glob(
                    "*.dxf"
                )
            ),
            "after": any(
                (
                    project_dir
                    / "dxf"
                    / "after"
                ).glob(
                    "*.dxf"
                )
            ),
        },
        "notes": [
            (
                "Все страницы и замечания "
                "подтверждены пользователем "
                "до формирования JSON."
            ),
            (
                "pdf_page — физический номер "
                "страницы PDF, начиная с 1."
            ),
            (
                "Соответствие BEFORE -> AFTER "
                "указано пользователем вручную."
            ),
            (
                "verified_fixed=false: "
                "факт устранения конкретного "
                "замечания пока автоматически "
                "не проверяется."
            ),
        ],
    }

    write_json(
        annotations_dir
        / "issues.json",
        issues_payload,
    )

    write_json(
        annotations_dir
        / "meta.json",
        meta_payload,
    )

    print()

    print(
        "[OK] Созданы:"
    )

    print(
        "  "
        + str(
            annotations_dir
            / "issues.json"
        )
    )

    print(
        "  "
        + str(
            annotations_dir
            / "meta.json"
        )
    )

    return True


def main() -> int:
    """Точка входа."""

    cases_dir = get_cases_dir()

    try:
        projects = find_projects(
            cases_dir
        )

    except RuntimeError as error:
        print(
            f"Ошибка: {error}",
            file=sys.stderr,
        )
        return 1

    print(
        "Каталог Базы Опыта:\n"
        f"{cases_dir}"
    )

    print()

    print(
        f"Найдено проектов: "
        f"{len(projects)}"
    )

    if not projects:
        return 0

    for index, project in enumerate(
        projects,
        start=1,
    ):
        print(
            f"  {index}. "
            f"{project.name}"
        )

    processed = 0
    skipped = 0
    errors = 0

    for index, project in enumerate(
        projects,
        start=1,
    ):
        try:
            processed_project = (
                process_project(
                    project_dir=project,
                    project_number=index,
                    projects_count=len(
                        projects
                    ),
                )
            )

            if processed_project:
                processed += 1
            else:
                skipped += 1

        except (
            RuntimeError,
            ValueError,
            fitz.FileDataError,
        ) as error:
            errors += 1

            print()

            print(
                f"[ERROR] "
                f"{project.name}: "
                f"{error}"
            )

    print()
    print("=" * 78)

    print(
        "Обработка завершена."
    )

    print(
        f"Успешно подготовлено: "
        f"{processed}"
    )

    print(
        f"Пропущено: "
        f"{skipped}"
    )

    print(
        f"Ошибок: "
        f"{errors}"
    )

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )