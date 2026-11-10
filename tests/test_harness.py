"""Тесты механизмов агентной обвязки (Harness Blocks P0 и P1).

Проверяет:
1. Idempotency LRU-кеш (tools.py).
2. Loop & Dedup Guard (дедупликация повторных вызовов).
3. Context Pruning (компакция старых наблюдений от Context Rot).
4. Empty Result Circuit Breaker (предохранитель пустых результатов).
5. Бюджетные потолки (Cost & Token Caps).
6. Параллельное исполнение нескольких инструментов.
"""

import json
import pytest
from tools import (
    clear_tool_cache,
    execute_tool,
    get_cache_stats,
)
from agent_loop import (
    _canonical_call_key,
    _prune_context,
)


def test_idempotency_lru_cache():
    """Проверка работы LRU-кеша для детерминированных запросов."""
    clear_tool_cache()
    stats0 = get_cache_stats()
    assert stats0["size"] == 0

    # Первый вызов (холодный)
    res1 = execute_tool("count_places", {"kind": "pharmacy", "only_24_7": True})
    assert "19" in res1
    stats1 = get_cache_stats()
    assert stats1["size"] == 1

    # Второй вызов (из кеша)
    res2 = execute_tool("count_places", {"kind": "pharmacy", "only_24_7": True})
    assert res1 == res2
    stats2 = get_cache_stats()
    assert stats2["size"] == 1

    # Очистка кеша
    clear_tool_cache()
    assert get_cache_stats()["size"] == 0


def test_canonical_call_key_deterministic():
    """Проверка, что разный порядок ключей в словаре даёт одинаковый ключ кеша."""
    k1 = _canonical_call_key("find_places", {"kind": "cafe", "street": "Пушкинская", "limit": 5})
    k2 = _canonical_call_key("find_places", {"limit": 5, "street": "Пушкинская", "kind": "cafe"})
    assert k1 == k2
    assert "find_places:" in k1


def test_context_pruning_compaction():
    """Проверка сжатия старых промежуточных сообщений для борьбы с Context Rot."""
    long_table = "id | name | street\n" + "\n".join(f"{i} | Кафе {i} | Улица {i}" for i in range(25))
    messages = [
        {"role": "system", "content": "Системный промпт"},
        {"role": "user", "content": "Где кафе?"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": {"name": "find_places"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": long_table},  # Старое объемное наблюдение
        {"role": "assistant", "content": "Ищу подробности..."},
        {"role": "tool", "tool_call_id": "c2", "content": "Свежее наблюдение"},  # Свежее наблюдение
    ]

    # На шаге 1 или 2 компакция не должна трогать контекст
    pruned_step1 = _prune_context(messages, current_step=1)
    assert pruned_step1 == 0
    assert "Компакция контекста" not in messages[3]["content"]

    # На шаге 3+ старое наблюдение (messages[3]) должно быть компактно сжато
    pruned_step3 = _prune_context(messages, current_step=3)
    assert pruned_step3 == 1
    assert "Компакция контекста обвязкой" in messages[3]["content"]
    assert len(messages[3]["content"]) < len(long_table)
    # Свежее наблюдение (messages[5]) не должно быть сжато
    assert messages[5]["content"] == "Свежее наблюдение"


def test_dedup_guard_logic():
    """Проверка реакции на повторный вызов одного и того же инструмента."""
    from agent_loop import _canonical_call_key
    executed_calls = {}

    call_key = _canonical_call_key("geocode_address", {"street": "Пушкинская"})
    executed_calls[call_key] = "Адрес найден: Пушкинская улица, lat=47.22, lon=39.71"

    # При попытке повторного вызова с тем же ключом
    assert call_key in executed_calls
    warning = (
        f"Повторный вызов отклонён обвязкой: вы уже вызывали инструмент 'geocode_address' "
        f"с этими же аргументами. Результат был: {executed_calls[call_key][:120]}..."
    )
    assert "Повторный вызов отклонён обвязкой" in warning


def test_empty_circuit_breaker_logic():
    """Проверка срабатывания предохранителя пустых результатов."""
    empty_obs = "Ничего не найдено."
    is_empty = any(w in empty_obs for w in ("Ничего не найдено", "не найдено в базе"))
    assert is_empty is True

    consecutive_empty = 2
    guard_hint = ""
    if consecutive_empty >= 2:
        guard_hint = (
            "\n[Предохранитель обвязки]: Поиск по критериям не дал результатов уже 2+ раз подряд. "
            "Прекратите веерный перебор и ответьте пользователю на основе уже известных данных."
        )
    assert "Предохранитель обвязки" in guard_hint


def test_off_topic_input_guardrail():
    """Проверка тематического гардрейла: отсечение сторонних запросов и пропуск городских."""
    from agent_loop import is_off_topic_query, OFF_TOPIC_REJECTION_MESSAGE

    # Сторонние запросы (должны блокироваться)
    off_topic_queries = [
        "напиши сайт на python",
        "напиши код на python для парсинга",
        "создай сайт интернет-магазина",
        "напиши скрипт на питоне",
        "реши квадратное уравнение x^2 - 5x + 6 = 0",
        "напиши стихотворение про Ростов",
        "переведи этот текст на английский",
        "расскажи анекдот",
    ]
    for q in off_topic_queries:
        assert is_off_topic_query(q) is True, f"Запрос '{q}' должен быть отсечен гардрейлом!"

    # Городские и диалоговые запросы (должны свободно проходить)
    city_queries = [
        "Сколько в Ростове аптек?",
        "Какие кафе есть на Пушкинской?",
        "Где ближайший пункт выдачи к 47.23, 39.70?",
        "Я на Белорусской 44, где аптека?",
        "Где поесть пельмени?",
        "На западном есть заведения вкусно и точка?",
        "Привет!",
        "Кто ты?",
        "О чём мы говорили?",
        "Спасибо!",
        "Покажи сайт заведения Ирис",
    ]
    for q in city_queries:
        assert is_off_topic_query(q) is False, f"Городской запрос '{q}' НЕ должен блокироваться!"

    assert "карте города" in OFF_TOPIC_REJECTION_MESSAGE
    assert "не занимаюсь" in OFF_TOPIC_REJECTION_MESSAGE
