# tests/functional/test_native_document_quality.py

"""Реквизиты и технические записи проверяются без экспертных подсказок и новых inference."""

from dataclasses import replace

import pytest
from pdrd_analysis_service.application.engineering_program import (
    program_findings,
    verified_programmatic_check,
)
from pdrd_analysis_service.application.use_cases.document_context import (
    DocumentPage,
    document_engineering_report,
)


def word(text, y, line):
    """Создаёт родное PDF-слово с физической областью, не данными экспертной матрицы."""
    return {
        "text": text,
        "block_no": 1,
        "line_no": line,
        "word_no": 0,
        "bbox": {"x_min": 100, "y_min": y, "x_max": 450, "y_max": y + 12},
    }


def findings(*pages, enabled=True):
    """Проходит существующий реестр и создание findings вместо тестирования одной регулярки."""
    report = document_engineering_report(pages, "independent-control", enabled=enabled)
    return tuple(
        f
        for f in program_findings(report)
        if any(
            origin.get("check", {}).get("rule") == "document_quality"
            for origin in f.origin_assertions
        )
    )


@pytest.mark.parametrize("designation", ["SDR 13.6-160х7,7", "SDR11-90x12"])
def test_inconsistent_pipe_dimensions_create_grounded_review_and_replay(designation):
    """Не назначает выдуманный сортамент и не повышает вопрос до нарушения без нормы."""
    result = findings(DocumentPage(9, "Труба ПЭ100 " + designation, "", ()))
    assert len(result) == 1
    finding = result[0]
    assert finding.status == "needs_review"
    assert finding.page == 9
    assert designation in finding.evidence
    assert verified_programmatic_check(finding)
    assert not verified_programmatic_check(replace(finding, status="confirmed"))
    assert not verified_programmatic_check(
        replace(finding, comment="Размер точно запрещён ГОСТ")
    )


@pytest.mark.parametrize(
    "designation", ["SDR11-110x10", "SDR 11-400×36,3", "SDR17-160x9,5"]
)
def test_nominal_pipe_rounding_is_not_an_error(designation):
    """Округление стенки и близкий ряд не превращаются в ложное замечание."""
    assert not findings(DocumentPage(1, designation, "", ()))


def test_document_part_code_has_both_real_pages_and_native_geometry():
    """Ожидаемый суффикс берётся из комплекта; ссылки не приписываются текущей странице."""
    code = "352/908-2-ИОС5.4"
    footer = word(code, 910, 1)
    result = findings(
        DocumentPage(2, code + "-ТЧ Текстовая часть", "", ()),
        DocumentPage(7, code, "", (), (footer,)),
    )
    assert len(result) == 1
    assert result[0].page == 7
    assert {s.page for s in result[0].document_context_basis_sources} == {2, 7}
    assert verified_programmatic_check(result[0])
    assert not findings(DocumentPage(7, code, "", (), (footer,)))
    assert not findings(
        DocumentPage(2, code + "-ТЧ", "", ()),
        DocumentPage(7, code, "", (), (footer,)),
        enabled=False,
    )


def test_contents_and_cover_are_not_mislabeled_as_text_part():
    """Обложка не является основной надписью ТЧ, а содержание имеет свой явный шифр."""
    code = "352/908-2-ИОС5.4"
    contents = f"Содержание тома\n{code}-С Содержание\n{code}-ТЧ Текстовая часть"
    result = findings(
        DocumentPage(1, code, "", (), (word(code, 900, 1),)),
        DocumentPage(2, contents, "", (), (word(code, 900, 1),)),
        DocumentPage(5, code, "", (), (word(code, 900, 1),)),
    )
    assert len(result) == 2
    assert not any(f.page == 1 for f in result)
    assert "содержания тома" in next(f for f in result if f.page == 2).comment
    assert "текстовой части" in next(f for f in result if f.page == 5).comment
    assert all(verified_programmatic_check(f) for f in result)


def test_ambiguous_part_code_uses_same_volume_manifest_without_inventing_a_norm():
    """Отдельные части названы в составе, но общий шифр на листе их не различает."""
    code = "392/117-5-ИОС4.9"
    manifest = (
        "Обозначение Наименование Примечание\nТекстовая часть\nПояснительная записка\n"
        "Графическая часть\nПлан оборудования\n" + code + "-С"
    )
    pages = (
        DocumentPage(1, code, "", ()),
        DocumentPage(2, manifest, "", ()),
        DocumentPage(6, code, "", (), (word(code, 910, 1),)),
    )
    result = findings(*pages)
    assert len(result) == 1 and result[0].page == 6
    assert result[0].status == "needs_review" and not result[0].basis_sources
    assert "ТЧ/ГЧ" in result[0].comment and verified_programmatic_check(result[0])
    assert {s.page for s in result[0].document_context_basis_sources} == {2, 6}
    assert not findings(*pages, enabled=False)
    combined = manifest.replace(
        "Текстовая часть\nПояснительная записка\nГрафическая часть",
        "Текстовая и графическая часть",
    )
    assert not findings(pages[0], DocumentPage(2, combined, "", ()), pages[2])
    foreign = manifest.replace(code, "392/118-5-ИОС4.9")
    assert not findings(pages[0], DocumentPage(2, foreign, "", ()), pages[2])


def test_expanded_abbreviation_and_model_mark_are_not_missing_definitions():
    """Перечень сокращений всего проекта и коды оборудования служат контрдоказательствами."""
    assert not findings(
        DocumentPage(1, "Термомеханическая установка (ТМУ).", "", ()),
        DocumentPage(4, "ТМУ обслуживается подготовленным персоналом.", "", ()),
    )
    assert not findings(
        DocumentPage(1, "Вентилятор ВР 400-46-6 установлен в помещении.", "", ())
    )
    assert not findings(
        DocumentPage(1, "Кран шаровой фланцевый КШЦФ.032.040, 12 штук.", "", ())
    )
    assert not findings(
        DocumentPage(1, "Клапан электромагнитный КЭМ.045 установлен на вводе.", "", ())
    )


def test_unexpanded_abbreviation_is_a_concrete_question_without_normative_verdict():
    """Уточнение буквального сокращения относится к исходной процедуре, а не к названию изделия."""
    result = findings(
        DocumentPage(
            4,
            "СЭ осуществляет обслуживание оборудования по утверждённой инструкции.",
            "",
            (),
        )
    )
    assert len(result) == 1
    assert result[0].status == "needs_review" and "СЭ" in result[0].comment
    assert verified_programmatic_check(result[0])


@pytest.mark.parametrize("prefix", ["далее – ", "далее — ", "далее по тексту: "])
def test_abbreviation_defined_with_further_marker_is_not_requested_again(prefix):
    """Явное «далее — …» раскрывает сокращение для последующих страниц проекта."""
    pages = (
        DocumentPage(4, f"Предусмотрены защитные конструкции ({prefix}ЗКТ).", "", ()),
        DocumentPage(
            7, "Защитные ЗКТ должны обеспечивать безопасность при эксплуатации.", "", ()
        ),
    )
    report = document_engineering_report(pages, "abbreviation-marker-control")
    assert not any(
        check.property_key == "abbreviation"
        and check.observations[0].fact.value_raw == "ЗКТ"
        for check in report.checks
    )


def test_complete_reference_on_another_page_prevents_edition_question():
    """Сокращённая ссылка допустима, когда полная редакция уже прочитана в проекте."""
    assert not findings(
        DocumentPage(1, "По СП 42.13330 приняты решения.", "", ()),
        DocumentPage(6, "Перечень: СП 42.13330.2016.", "", ()),
    )
    assert not findings(
        DocumentPage(1, "Принят СП 255.1325800.2016 для проверки эксплуатации.", "", ())
    )
    assert not findings(DocumentPage(1, "В перечне указан СП 2.6.1.2612-10.", "", ()))


def test_counted_fire_storage_question_is_bound_to_each_actual_design_occurrence():
    """Вопрос о составе не переезжает только на последний лист с другой формой записи."""
    from pdrd_analysis_service.domain.engineering_facts import (
        merge_text_engineering_facts,
    )

    text = (
        "Резервный источник противопожарного водоснабжения: установка резервуаров запаса воды: "
        "два резервуара объемом 80 м3 и один резервуар объемом 40 м3."
    )
    pages = tuple(
        DocumentPage(page, text, "", merge_text_engineering_facts((), text, page))
        for page in [4, 11]
    )
    report = document_engineering_report(pages, "counted-storage-control")
    actual = [
        f
        for f in program_findings(report)
        if any(
            o.get("check", {}).get("calculation", {}).get("dependency")
            == "fire_storage_basis"
            for o in f.origin_assertions
        )
    ]
    assert actual
    assert {s.page for f in actual for s in f.document_context_basis_sources} == {4, 11}
    assert all(f.status == "needs_review" for f in actual)
    assert all(verified_programmatic_check(f) for f in actual)
    assert not any("недостаточен" in f.comment.lower() for f in actual)


def test_section_code_comparison_uses_cover_and_actual_footer_not_filename():
    """Проверяет конкретный номер раздела, сохраняя обе исходные страницы."""
    cover = "352/908-2-ИОС6.7"
    wrong = "352/908-2-ИОС4.8"
    result = findings(
        DocumentPage(1, cover, "", ()),
        DocumentPage(12, wrong, "", (), (word(wrong, 910, 1),)),
    )
    assert len(result) == 1
    assert result[0].page == 12 and result[0].status == "needs_review"
    assert {s.page for s in result[0].document_context_basis_sources} == {1, 12}
    assert verified_programmatic_check(result[0])
    # Суффикс графической части и самостоятельная ссылка в тексте допустимы.
    assert not findings(
        DocumentPage(1, cover, "", ()),
        DocumentPage(12, cover + "-ГЧ", "", (), (word(cover + "-ГЧ", 910, 1),)),
    )
    assert not findings(
        DocumentPage(1, cover, "", ()),
        DocumentPage(12, wrong, "", (), (word(wrong, 200, 1),)),
    )
    result = findings(
        DocumentPage(1, cover + "Взам.инв.", "", (), (word(cover, 550, 1),)),
        DocumentPage(12, wrong, "", (), (word(wrong, 910, 1),)),
    )
    assert len(result) == 1 and verified_programmatic_check(result[0])


def test_project_title_comparison_requires_same_object_and_literal_cover():
    """Согласование реквизита не переносит название с другого строительного объекта."""
    cover = "Комплекс зданий и сооружений объекта 352/908-2"
    footer = "Комплекс зданий и сооружений Объекте (шифр объекта 352/908-2)"
    result = findings(
        DocumentPage(1, cover, "", (), (word(cover, 450, 1),)),
        DocumentPage(8, footer, "", (), (word(footer, 850, 1),)),
    )
    assert len(result) == 1 and result[0].status == "needs_review"
    assert result[0].page == 8
    assert {s.page for s in result[0].document_context_basis_sources} == {1, 8}
    assert verified_programmatic_check(result[0])
    assert not findings(
        DocumentPage(1, cover, "", (), (word(cover, 450, 1),)),
        DocumentPage(8, cover.upper(), "", (), (word(cover.upper(), 850, 1),)),
    )
    other = footer.replace("352/908-2", "352/909-3")
    assert not findings(
        DocumentPage(1, cover, "", (), (word(cover, 450, 1),)),
        DocumentPage(8, other, "", (), (word(other, 850, 1),)),
    )
    assert not findings(
        DocumentPage(1, cover, "", (), (word(cover, 450, 1),)),
        DocumentPage(8, footer, "", (), (word(footer, 850, 1),)),
        enabled=False,
    )


def test_brand_and_table_total_are_not_unexpanded_abbreviations():
    """Капитализация слова и марка контроллера не означают инженерное сокращение."""
    assert not findings(
        DocumentPage(3, "Управление осуществляется на базе контроллера АЛЬФА.", "", ())
    )
    assert not findings(
        DocumentPage(
            3, "ИТОГО в таблице представлены результаты расчёта оборудования.", "", ()
        )
    )
    result = findings(
        DocumentPage(
            3,
            "Если производителем не определены работы, ИТСО обслуживается регулярно.",
            "",
            (),
        )
    )
    assert any("ИТСО" in f.comment for f in result)


@pytest.mark.parametrize(
    "instruction",
    ["Каждый лифт должен иметь акт приёмки.", "Очистка производится продувкой паром."],
)
def test_special_system_instruction_remains_a_question_about_applicability(instruction):
    """Не объявляет специальную систему отсутствующей по одному назначению здания."""
    result = findings(
        DocumentPage(1, "Блочно-модульная котельная", "", ()),
        DocumentPage(7, instruction, "", ()),
    )
    assert len(result) == 1 and result[0].status == "needs_review"
    assert result[0].page == 7 and verified_programmatic_check(result[0])
    assert "применимость" in result[0].comment


def test_table_title_on_separate_line_is_counterevidence_to_missing_caption():
    """Не требует название повторно, если между номером и шапкой прочитан заголовок."""
    numbered = word("Таблица 7", 100, 1)
    named = word("Тепловые нагрузки здания", 120, 2)
    headers = word("Наименование показателя", 142, 3)
    assert not findings(
        DocumentPage(
            5,
            "Таблица 7\nТепловые нагрузки здания\nНаименование показателя",
            "",
            (),
            (numbered, named, headers),
        )
    )


@pytest.mark.parametrize(
    "left,right",
    [
        ("Расход теплоты, Вт", "Установленная мощность"),
        ("Расчётная температура наружного воздуха", "Расход воздуха"),
    ],
)
def test_right_aligned_table_number_does_not_hide_left_column_headers(left, right):
    """Нативные колонки всей таблицы используются независимо от выравнивания её номера."""
    numbered = word("Таблица 8.2", 100, 1)
    numbered["bbox"].update(x_min=810, x_max=910)
    first = word(left, 130, 2)
    first["bbox"].update(x_min=160, x_max=380)
    second = word(right, 130, 3)
    second["bbox"].update(x_min=680, x_max=900)
    result = findings(
        DocumentPage(
            7, f"Таблица 8.2\n{left} {right}", "", (), (numbered, first, second)
        )
    )
    assert len(result) == 1 and result[0].page == 7
    assert "наименование" in result[0].comment
    assert verified_programmatic_check(result[0])


@pytest.mark.parametrize(
    "title", ["Расход воздуха в помещениях, м3/ч", "Температура воздуха"]
)
def test_left_aligned_content_title_is_not_a_column_header(title):
    """Наличие названия между номером справа и шапкой исключает повторный вопрос."""
    numbered = word("Таблица 8.2", 100, 1)
    numbered["bbox"].update(x_min=810, x_max=910)
    named = word(title, 120, 2)
    named["bbox"].update(x_min=160, x_max=390)
    first = word("Наименование", 140, 3)
    second = word("Значение показателя", 140, 4)
    second["bbox"].update(x_min=680, x_max=900)
    assert not findings(
        DocumentPage(
            7,
            f"Таблица 8.2\n{title}\nНаименование Значение показателя",
            "",
            (),
            (numbered, named, first, second),
        )
    )


@pytest.mark.parametrize("caption", ["Таблица 8.", "Таблица 8.2."])
def test_terminal_period_does_not_hide_caption_or_invalidate_present_title(caption):
    """Точка после номера не скрывает таблицу и сама по себе не является замечанием."""
    numbered = word(caption, 100, 1)
    left = word("Наименование", 122, 2)
    right = word("Значение показателя", 122, 3)
    right["bbox"].update(x_min=680, x_max=900)
    result = findings(
        DocumentPage(
            7,
            f"{caption}\nНаименование Значение показателя",
            "",
            (),
            (numbered, left, right),
        )
    )
    assert len(result) == 1 and "наименование" in result[0].comment
    title = word("Показатели работы системы", 116, 4)
    assert not findings(
        DocumentPage(
            7, f"{caption}\nПоказатели работы системы", "", (), (numbered, title)
        )
    )


def test_wrapped_column_header_and_content_title_have_different_native_geometry():
    """Центрированные колонки с разной высотой строки не смешиваются с общим названием."""
    numbered = word("Таблица 6", 100, 1)
    numbered["bbox"].update(x_min=810, x_max=910)
    period = word("Периодичность", 120, 2)
    period["bbox"].update(x_min=720, x_max=900, y_max=140)
    elements = word("Элементы и помещения", 130, 3)
    elements["bbox"].update(x_min=180, x_max=570, y_max=150)
    result = findings(
        DocumentPage(
            7,
            "Таблица 6\nПериодичность Элементы и помещения",
            "",
            (),
            (numbered, period, elements),
        )
    )
    assert len(result) == 1
    title = word("Средняя температура воздуха по месяцам", 120, 4)
    title["bbox"].update(x_min=180, x_max=820, y_max=140)
    months = (word("Январь", 150, 5), word("Февраль", 150, 6))
    months[1]["bbox"].update(x_min=680, x_max=900)
    assert not findings(
        DocumentPage(
            7,
            "Таблица 6\nСредняя температура воздуха по месяцам\nЯнварь Февраль",
            "",
            (),
            (numbered, title, *months),
        )
    )


def test_internal_spelling_variant_uses_project_evidence_without_a_dictionary_of_answers():
    """Не объявляет редкое слово ошибкой; сохраняет редкий вариант и все страницы образца."""
    pages = (
        DocumentPage(
            2,
            "оборудования оборудования",
            "",
            (),
            (word("оборудования", 100, 1), word("оборудования", 120, 2)),
        ),
        DocumentPage(
            4,
            "оборудования оборудования",
            "",
            (),
            (word("оборудования", 100, 1), word("оборудования", 120, 2)),
        ),
        DocumentPage(7, "обоурдования", "", (), (word("обоурдования", 100, 1),)),
    )
    result = findings(*pages)
    assert len(result) == 1 and result[0].status == "needs_review"
    assert result[0].page == 7 and "обоурдования" in result[0].comment
    assert {s.page for s in result[0].document_context_basis_sources} == {2, 4, 7}
    assert verified_programmatic_check(result[0])
    assert not findings(*pages, enabled=False)
    assert not findings(pages[0], pages[2])


@pytest.mark.parametrize(
    "first,second",
    [
        ("оборудования", "оборудование"),
        ("соответствовало", "соответствовать"),
        ("МКУ-20", "МКУ-24"),
        ("водоснабжение", "водоотведение"),
        ("провести", "привести"),
        ("проведения", "приведения"),
        ("контрактов", "контактов"),
        ("принимаются", "принимается"),
    ],
)
def test_different_meaning_ending_or_equipment_code_is_not_a_spelling_variant(
    first, second
):
    """Близость строк не позволяет менять падеж, назначение системы или параметры модели."""
    from pdrd_analysis_service.domain.document_term_variants import (
        internal_typo_variant,
    )

    assert not internal_typo_variant(first, second)


def test_independent_tables_with_same_number_are_not_merged_across_appendices():
    """Номер таблицы не означает один объект без доказанного продолжения."""
    from pdrd_analysis_service.application.use_cases.document_context import (
        merge_cross_page_findings,
    )

    second_header = word("Значение", 120, 3)
    second_header["bbox"].update(x_min=680, x_max=900)
    pages = tuple(
        DocumentPage(
            page,
            "Таблица 1\nНаименование показателя Значение",
            "",
            (),
            (
                word("Таблица 1", 100, 1),
                word("Наименование показателя", 120, 2),
                second_header,
            ),
        )
        for page in [5, 18]
    )
    drafts = findings(*pages)
    assert len(drafts) == 2
    merged = merge_cross_page_findings(page_findings={}, cross_findings=drafts)
    assert set(merged) == {5, 18}
    assert sum(map(len, merged.values())) == 2


def test_project_identity_context_is_literal_and_cannot_supply_a_normative_basis():
    """Назначение проекта сопровождает удалённый лист без выдуманного перечня отсутствующих систем."""
    from types import SimpleNamespace

    from pdrd_analysis_service.application.use_cases.document_context import (
        build_page_document_context,
    )

    cover = (
        "ПРОЕКТНАЯ ДОКУМЕНТАЦИЯ\nШифр объекта: 352/908\nПромышленная насосная станция"
    )
    contexts = build_page_document_context(
        pages=(
            DocumentPage(1, cover, "", ()),
            DocumentPage(18, "Порядок эксплуатации", "", ()),
        ),
        semantic_by_page={},
        options=SimpleNamespace(
            max_related_facts_per_page=8,
            max_text_sources_per_page=3,
            semantic_threshold=0.5,
        ),
    )
    identity = next(s for s in contexts[18] if s.match_type == "document_identity")
    assert identity.page == 1 and identity.text == cover
    assert identity.source_id.startswith("D-")


def test_disabled_document_context_does_not_apply_cover_scope_to_other_pages():
    """D=false не переносит назначение с обложки на самостоятельную проверку другого листа."""
    pages = (
        DocumentPage(1, "Блочно-модульная котельная", "", ()),
        DocumentPage(
            8,
            "В жилых домах необходимо проверять оборудование по установленному порядку.",
            "",
            (),
        ),
    )
    assert any("применимость" in f.comment for f in findings(*pages))
    assert not findings(*pages, enabled=False)
