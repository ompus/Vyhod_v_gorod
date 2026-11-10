"""Набор из 10 тестовых задач для оценки агента и проведения A/B-тестирования.

Каждая задача специфицирует:
- task_id: уникальный идентификатор задачи;
- question: формулировка вопроса пользователя;
- expected_tools: ожидаемый инструмент или цепочка инструментов;
- check_tool_and_args: функция валидации выбора инструмента и аргументов;
- check_outcome: функция верификации итогового ответа по эталону (ground truth).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class BenchmarkTask:
    task_id: str
    title: str
    question: str
    expected_tools: list[str]
    check_tool_and_args: Callable[[list[dict[str, Any]]], tuple[bool, str]]
    check_outcome: Callable[[str, list[dict[str, Any]]], tuple[bool, str]]


# ---------------------------------------------------------------------------
# ПРОВЕРКИ ДЛЯ 10 ЗАДАЧ
# ---------------------------------------------------------------------------

# Задача 1: Круглосуточные аптеки (подсчет с фильтром)
def _check_args_t1(calls: list[dict[str, Any]]) -> tuple[bool, str]:
    if not calls:
        return False, "Инструменты не вызывались"
    c = calls[0]
    if c["name"] != "count_places":
        return False, f"Ожидался count_places, вызван {c['name']}"
    k = str(c["args"].get("kind", "")).lower()
    if k not in ("pharmacy", "аптека"):
        return False, f"Неверный kind='{k}' (ожидался pharmacy)"
    if not c["args"].get("only_24_7"):
        return False, "Параметр only_24_7 не установлен в True"
    return True, "Инструмент и аргументы верны"


def _check_outcome_t1(answer: str, calls: list[dict[str, Any]]) -> tuple[bool, str]:
    if "19" in answer:
        return True, "В ответе присутствует верное число 19"
    return False, "В ответе отсутствует число 19"


# Задача 2: Количество школ (простой подсчет)
def _check_args_t2(calls: list[dict[str, Any]]) -> tuple[bool, str]:
    if not calls:
        return False, "Инструменты не вызывались"
    c = calls[0]
    if c["name"] != "count_places":
        return False, f"Ожидался count_places, вызван {c['name']}"
    k = str(c["args"].get("kind", "")).lower()
    if k not in ("school", "школа"):
        return False, f"Неверный kind='{k}' (ожидался school)"
    return True, "Инструмент и аргументы верны"


def _check_outcome_t2(answer: str, calls: list[dict[str, Any]]) -> tuple[bool, str]:
    if "107" in answer:
        return True, "В ответе присутствует верное число 107"
    return False, "В ответе отсутствует число 107"


# Задача 3: Количество пунктов выдачи заказов
def _check_args_t3(calls: list[dict[str, Any]]) -> tuple[bool, str]:
    if not calls:
        return False, "Инструменты не вызывались"
    c = calls[0]
    if c["name"] != "count_places":
        return False, f"Ожидался count_places, вызван {c['name']}"
    k = str(c["args"].get("kind", "")).lower()
    if k not in ("outpost", "пункт выдачи"):
        return False, f"Неверный kind='{k}' (ожидался outpost)"
    return True, "Инструмент и аргументы верны"


def _check_outcome_t3(answer: str, calls: list[dict[str, Any]]) -> tuple[bool, str]:
    if "922" in answer:
        return True, "В ответе присутствует верное число 922"
    return False, "В ответе отсутствует число 922"


# Задача 4: Кафе на Пушкинской улице
def _check_args_t4(calls: list[dict[str, Any]]) -> tuple[bool, str]:
    if not calls:
        return False, "Инструменты не вызывались"
    c = calls[0]
    if c["name"] != "find_places":
        return False, f"Ожидался find_places, вызван {c['name']}"
    k = str(c["args"].get("kind", "")).lower()
    if k not in ("cafe", "кафе"):
        return False, f"Неверный kind='{k}' (ожидался cafe)"
    st = str(c["args"].get("street", "")).lower()
    if "пушкин" not in st:
        return False, f"Неверная улица: street='{st}'"
    return True, "Инструмент и аргументы верны"


def _check_outcome_t4(answer: str, calls: list[dict[str, Any]]) -> tuple[bool, str]:
    ans_lower = answer.lower()
    known = ["тень", "coffee man", "булкин", "бар здоровых привычек"]
    if any(k in ans_lower for k in known):
        return True, "В ответе названы реальные кафе с Пушкинской улицы"
    return False, "Не найдены ожидаемые кафе с Пушкинской улицы"


# Задача 5: Супермаркеты на улице Доватора
def _check_args_t5(calls: list[dict[str, Any]]) -> tuple[bool, str]:
    if not calls:
        return False, "Инструменты не вызывались"
    c = calls[0]
    if c["name"] != "find_places":
        return False, f"Ожидался find_places, вызван {c['name']}"
    k = str(c["args"].get("kind", "")).lower()
    if k not in ("supermarket", "супермаркет"):
        return False, f"Неверный kind='{k}' (ожидался supermarket)"
    st = str(c["args"].get("street", "")).lower()
    if "доватор" not in st:
        return False, f"Неверная улица: street='{st}'"
    return True, "Инструмент и аргументы верны"


def _check_outcome_t5(answer: str, calls: list[dict[str, Any]]) -> tuple[bool, str]:
    ans_lower = answer.lower()
    known = ["чижик", "metro", "метро", "лента"]
    if any(k in ans_lower for k in known):
        return True, "В ответе названы реальные супермаркеты на Доватора"
    return False, "Не найдены ожидаемые супермаркеты на Доватора"


# Задача 6: Топ брендов пунктов выдачи
def _check_args_t6(calls: list[dict[str, Any]]) -> tuple[bool, str]:
    if not calls:
        return False, "Инструменты не вызывались"
    c = calls[0]
    if c["name"] != "top_brands":
        return False, f"Ожидался top_brands, вызван {c['name']}"
    k = str(c["args"].get("kind", "")).lower()
    if k not in ("outpost", "пункт выдачи"):
        return False, f"Неверный kind='{k}' (ожидался outpost)"
    return True, "Инструмент и аргументы верны"


def _check_outcome_t6(answer: str, calls: list[dict[str, Any]]) -> tuple[bool, str]:
    ans_lower = answer.lower()
    if "ozon" in ans_lower or "озон" in ans_lower or "wildberries" in ans_lower or "вайлдберриз" in ans_lower or "авито" in ans_lower:
        return True, "В ответе названы лидеры среди ПВЗ (Ozon/WB/Авито)"
    return False, "В ответе не упомянуты лидирующие бренды ПВЗ"


# Задача 7: Популярные сети супермаркетов
def _check_args_t7(calls: list[dict[str, Any]]) -> tuple[bool, str]:
    if not calls:
        return False, "Инструменты не вызывались"
    c = calls[0]
    if c["name"] != "top_brands":
        return False, f"Ожидался top_brands, вызван {c['name']}"
    k = str(c["args"].get("kind", "")).lower()
    if k not in ("supermarket", "супермаркет"):
        return False, f"Неверный kind='{k}' (ожидался supermarket)"
    return True, "Инструмент и аргументы верны"


def _check_outcome_t7(answer: str, calls: list[dict[str, Any]]) -> tuple[bool, str]:
    ans_lower = answer.lower()
    if "пятёрочка" in ans_lower or "пятерочка" in ans_lower or "магнит" in ans_lower:
        return True, "В ответе названы крупнейшие сети супермаркетов (Пятёрочка/Магнит)"
    return False, "В ответе не названы лидеры сетей супермаркетов"


# Задача 8: Ближайший банк к координатам (47.2200, 39.7100)
def _check_args_t8(calls: list[dict[str, Any]]) -> tuple[bool, str]:
    if not calls:
        return False, "Инструменты не вызывались"
    c = calls[0]
    if c["name"] != "nearest_place":
        return False, f"Ожидался nearest_place, вызван {c['name']}"
    k = str(c["args"].get("kind", "")).lower()
    if k not in ("bank", "банк"):
        return False, f"Неверный kind='{k}' (ожидался bank)"
    lat = float(c["args"].get("lat", 0))
    lon = float(c["args"].get("lon", 0))
    if abs(lat - 47.2200) > 0.01 or abs(lon - 39.7100) > 0.01:
        return False, f"Координаты искажены: ({lat}, {lon})"
    return True, "Инструмент и аргументы верны"


def _check_outcome_t8(answer: str, calls: list[dict[str, Any]]) -> tuple[bool, str]:
    ans_lower = answer.lower()
    if "сбер" in ans_lower or "сбербанк" in ans_lower or "134" in ans_lower or "130" in ans_lower or "135" in ans_lower:
        return True, "В ответе указан Сбербанк или точное расстояние ~134 м"
    return False, "В ответе отсутствует Сбербанк или расстояние до него"


# Задача 9: Ближайшая аптека к точке (47.2357, 39.7015)
def _check_args_t9(calls: list[dict[str, Any]]) -> tuple[bool, str]:
    if not calls:
        return False, "Инструменты не вызывались"
    c = calls[0]
    if c["name"] != "nearest_place":
        return False, f"Ожидался nearest_place, вызван {c['name']}"
    k = str(c["args"].get("kind", "")).lower()
    if k not in ("pharmacy", "аптека"):
        return False, f"Неверный kind='{k}' (ожидался pharmacy)"
    lat = float(c["args"].get("lat", 0))
    lon = float(c["args"].get("lon", 0))
    if abs(lat - 47.2357) > 0.01 or abs(lon - 39.7015) > 0.01:
        return False, f"Координаты искажены: ({lat}, {lon})"
    return True, "Инструмент и аргументы верны"


def _check_outcome_t9(answer: str, calls: list[dict[str, Any]]) -> tuple[bool, str]:
    # Расстояние до ближайшей аптеки составляет около 160 метров (ID 2137)
    if any(d in answer for d in ("160", "161", "159", "162", "158")):
        return True, "В ответе указано точное расстояние около 160 м"
    return False, "В ответе отсутствует расстояние ~160 м"


# Задача 10: Многошаговая цепочка (Белорусская 44 -> аптека -> часы)
def _check_args_t10(calls: list[dict[str, Any]]) -> tuple[bool, str]:
    names = [c["name"] for c in calls]
    if "geocode_address" not in names:
        return False, f"В цепочке отсутствует geocode_address (было: {names})"
    if "nearest_place" not in names:
        return False, f"В цепочке отсутствует nearest_place (было: {names})"

    # Проверка порядка: геокод должен идти раньше поиска ближайшего
    geo_idx = names.index("geocode_address")
    near_idx = names.index("nearest_place")
    if geo_idx > near_idx:
        return False, "geocode_address должен вызываться до nearest_place"

    geo_args = calls[geo_idx]["args"]
    st = str(geo_args.get("street", "")).lower()
    if "белорус" not in st:
        return False, f"В geocode передана неверная улица: '{st}'"

    near_args = calls[near_idx]["args"]
    k = str(near_args.get("kind", "")).lower()
    if k not in ("pharmacy", "аптека"):
        return False, f"В nearest_place передан неверный kind='{k}'"

    return True, "Цепочка инструментов и аргументы корректны"


def _check_outcome_t10(answer: str, calls: list[dict[str, Any]]) -> tuple[bool, str]:
    ans_lower = answer.lower()
    # Аптека «Лаки Фарма», часы: Mo-Su 08:00-21:00, расстояние ~123-136 м
    has_pharmacy = "лаки" in ans_lower or "фарма" in ans_lower or "1728" in answer
    has_hours = "21" in answer or "08:00" in answer or "21:00" in answer
    if has_pharmacy and has_hours:
        return True, "В ответе верно указана аптека Лаки Фарма и часы работы (до 21:00)"
    if has_hours:
        return True, "В ответе верно указано время работы (до 21:00)"
    return False, "В ответе отсутствует название аптеки или режим работы"


# ---------------------------------------------------------------------------
# РЕЕСТР 10 ЭТАЛОННЫХ ЗАДАЧ
# ---------------------------------------------------------------------------

BENCHMARK_TASKS: list[BenchmarkTask] = [
    BenchmarkTask(
        task_id="task_01_count_24_7_pharmacies",
        title="Круглосуточные аптеки (подсчет)",
        question="Сколько в Ростове круглосуточных аптек?",
        expected_tools=["count_places"],
        check_tool_and_args=_check_args_t1,
        check_outcome=_check_outcome_t1,
    ),
    BenchmarkTask(
        task_id="task_02_count_schools",
        title="Количество школ города",
        question="Сколько всего школ зарегистрировано в Ростове-на-Дону?",
        expected_tools=["count_places"],
        check_tool_and_args=_check_args_t2,
        check_outcome=_check_outcome_t2,
    ),
    BenchmarkTask(
        task_id="task_03_count_outposts",
        title="Количество пунктов выдачи заказов",
        question="Сколько пунктов выдачи заказов (outpost) есть в городе?",
        expected_tools=["count_places"],
        check_tool_and_args=_check_args_t3,
        check_outcome=_check_outcome_t3,
    ),
    BenchmarkTask(
        task_id="task_04_find_cafes_pushkinskaya",
        title="Кафе на Пушкинской",
        question="Какие кафе есть на Пушкинской улице?",
        expected_tools=["find_places"],
        check_tool_and_args=_check_args_t4,
        check_outcome=_check_outcome_t4,
    ),
    BenchmarkTask(
        task_id="task_05_find_supermarkets_dovatora",
        title="Супермаркеты на Доватора",
        question="Перечисли супермаркеты на улице Доватора.",
        expected_tools=["find_places"],
        check_tool_and_args=_check_args_t5,
        check_outcome=_check_outcome_t5,
    ),
    BenchmarkTask(
        task_id="task_06_top_outpost_brands",
        title="Топ брендов ПВЗ",
        question="Какие сети пунктов выдачи чаще всего встречаются в Ростове? Назови лидеров.",
        expected_tools=["top_brands"],
        check_tool_and_args=_check_args_t6,
        check_outcome=_check_outcome_t6,
    ),
    BenchmarkTask(
        task_id="task_07_top_supermarket_brands",
        title="Топ сетей супермаркетов",
        question="Какие торговые сети супермаркетов наиболее популярны в городе? Покажи топ сетей.",
        expected_tools=["top_brands"],
        check_tool_and_args=_check_args_t7,
        check_outcome=_check_outcome_t7,
    ),
    BenchmarkTask(
        task_id="task_08_nearest_bank_coords",
        title="Ближайший банк к координатам",
        question="Какой банк ближе всего к точке с координатами 47.2200, 39.7100?",
        expected_tools=["nearest_place"],
        check_tool_and_args=_check_args_t8,
        check_outcome=_check_outcome_t8,
    ),
    BenchmarkTask(
        task_id="task_09_nearest_pharmacy_coords",
        title="Ближайшая аптека к точке",
        question="Какая аптека ближе всего к координатам 47.2357, 39.7015 и сколько до неё метров?",
        expected_tools=["nearest_place"],
        check_tool_and_args=_check_args_t9,
        check_outcome=_check_outcome_t9,
    ),
    BenchmarkTask(
        task_id="task_10_chain_belorusskaya",
        title="Цепочка: адрес -> аптека -> часы работы",
        question="Я нахожусь на Белорусской улице, 44. Где ближайшая аптека и до скольки она сегодня работает?",
        expected_tools=["geocode_address", "nearest_place"],
        check_tool_and_args=_check_args_t10,
        check_outcome=_check_outcome_t10,
    ),
]
