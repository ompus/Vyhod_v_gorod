from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncGenerator

from agent_loop import stream_agent_run, get_client
from app.config import (
    MODEL,
    API_KEY,
    BASE_URL,
    SERVICE,
)
from tools import current_discovered_places

logger = logging.getLogger(__name__)

# Initialize client verification if API Key is configured
_client_configured = False


def setup_agents_sdk() -> None:
    """Инициализация агентного движка"""
    global _client_configured
    if _client_configured:
        return

    try:
        client, model = get_client()
        logger.info(f"Клиент создан. Модель: {model}, URL: {client.base_url}")
    except Exception as e:
        logger.warning(f"Ключ API не настроен ({e}), запросы будут перенаправлены на локальный агент.")

    _client_configured = True


async def stream_agent_conversation(
    conversation_id: str,
    user_prompt: str,
    history: list[dict[str, Any]],
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Выполняет агентный запрос через единый non-framework цикл agent_loop
    и стримит живые SSE-события:
    - event: thinking (шаги размышлений)
    - event: tool_call (вызов инструмента)
    - event: tool_result (результат инструмента)
    - event: places (структурированные карточки мест)
    - event: token (посимвольный вывод ответа)
    - event: done (завершение)
    """
    async for item in stream_agent_run(question=user_prompt, history=history):
        yield item


async def _safe_put(queue: asyncio.Queue | None, event: dict[str, Any]) -> None:
    if queue is not None:
        await queue.put(event)


async def run_local_agent(
    query: str,
    queue: asyncio.Queue | None = None,
    thoughts: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Intelligent local agent controller.
    Analyzes user intent:
    0. Conversational dialogue & History recall (e.g. 'О чём мы общались сейчас?', 'Привет', 'Кто ты?')
    1. Place details by ID or Name (e.g. 'Расскажи подробнее про заведение Ирис (ID 1687)')
    2. Route / Distance (e.g. 'Как добраться до Ирис...')
    3. Geocoding & proximity (e.g. 'недалеко от адреса Города Волос 8, где можно вкусно поесть')
    4. Street search (e.g. 'кафе на Пушкинской')
    5. Semantic search (e.g. 'пельмени', 'кофе и круассаны', 'хинкали')
    """
    if thoughts is None:
        thoughts = []
    from app.agent.tools import (
        do_geocode_address,
        do_find_nearest_places,
        do_find_places,
        do_get_place_details,
        query_poi,
        distance_m,
        current_discovered_places,
    )

    q_lower = query.lower().strip()

    # C1. Questions about conversation history
    history_patterns = [
        "о чём мы", "о чем мы", "что мы обсуждали", "о чем говорили", "напомни о чем",
        "что было раньше", "что ты мне советовал", "какие заведения ты предлагал",
        "о чем разговор", "о чем шла речь", "повтори последнее"
    ]
    if any(p in q_lower for p in history_patterns):
        step = {
            "step": len(thoughts) + 1,
            "title": "Анализ истории диалога",
            "detail": "Просмотр контекста предыдущих сообщений диалога...",
        }
        thoughts.append(step)
        await _safe_put(queue, {"event": "thinking", "data": step})
        await asyncio.sleep(0.2)

        if history:
            user_topics = []
            for h in history:
                if h.get("role") == "user" and h.get("content"):
                    c = h.get("content").strip()
                    if not any(p in c.lower() for p in history_patterns):
                        user_topics.append(c)

            if user_topics:
                recent_topics = user_topics[-3:]
                topics_str = "\n".join(f"- «{t}»" for t in recent_topics)
                reply = (
                    f"Ранее в нашем диалоге мы обсуждали:\n\n{topics_str}\n\n"
                    f"Я готов продолжить! Вы можете уточнить детали по любому найденному месту (часы работы, телефон, адрес), "
                    f"попросить проложить маршрут или найти заведения в другой части Ростова-на-Дону."
                )
            else:
                reply = "Мы только что начали общение и пока не успели детально обсудить заведения. Спросите меня, например, о ресторанах рядом с нужным адресом или о конкретных блюдах в Ростове!"
        else:
            reply = "Мы только начали наш разговор! Вы пока не задавали вопросов о заведениях Ростова-на-Дону. Чем я могу помочь вам прямо сейчас?"

        return reply, []

    # C2. Greetings and bot introduction
    greeting_patterns = ["привет", "здравствуй", "добрый день", "доброе утро", "добрый вечер", "хай", "салют", "хей"]
    meta_patterns = ["кто ты", "что ты умеешь", "что ты за бот", "расскажи о себе", "как ты работаешь"]
    gratitude_patterns = ["спасибо", "благодарю", "отлично", "супер", "классно", "понял"]

    if any(re.search(rf"\b{re.escape(p)}\b", q_lower) for p in greeting_patterns) and len(q_lower.split()) <= 4:
        step = {
            "step": len(thoughts) + 1,
            "title": "Приветствие",
            "detail": "Формирование приветственного сообщения без вызова поисковых инструментов.",
        }
        thoughts.append(step)
        await _safe_put(queue, {"event": "thinking", "data": step})
        await asyncio.sleep(0.2)
        reply = (
            "Привет! Я ваш персональный ИИ-гид по Ростову-на-Дону на базе 8860 заведений из OpenStreetMap.\n\n"
            "Я умею:\n"
            "- Находить заведения в шаговой доступности от любого адреса с точным расстоянием в метрах (например: *«недалеко от Пушкинской 44»*)\n"
            "- Искать по блюдам и атмосфере, если вы забыли название (например: *«где поесть пельмени»* или *«уютная кофейня»*)\n"
            "- Показывать точные часы работы, телефоны, сайты и адреса заведений\n\n"
            "Чем могу помочь сегодня?"
        )
        return reply, []

    if any(p in q_lower for p in meta_patterns):
        step = {
            "step": len(thoughts) + 1,
            "title": "Описание возможностей",
            "detail": "Формирование справки по возможностям гида...",
        }
        thoughts.append(step)
        await _safe_put(queue, {"event": "thinking", "data": step})
        await asyncio.sleep(0.2)
        reply = (
            "Я — интеллектуальный городской гид по Ростову-на-Дону.\n\n"
            "В моём распоряжении полная городская база (POI) и семантический векторный поиск ChromaDB. Вы можете:\n"
            "1. **Найти места рядом с адресом:** «вкусно поесть около Садовой 10»\n"
            "2. **Найти по смыслу или кухне:** «хинкали», «сочные бургеры», «свежая выпечка»\n"
            "3. **Узнать подробности:** «подробнее про заведение Ирис» или нажать кнопку в карточке\n"
            "4. **Свободно общаться:** задавать любые вопросы по ходу диалога!\n\n"
            "Куда бы вам хотелось сходить?"
        )
        return reply, []

    if any(re.search(rf"\b{re.escape(p)}\b", q_lower) for p in gratitude_patterns) and len(q_lower.split()) <= 4:
        step = {
            "step": len(thoughts) + 1,
            "title": "Ответ пользователю",
            "detail": "Ответ на благодарность...",
        }
        thoughts.append(step)
        await _safe_put(queue, {"event": "thinking", "data": step})
        await asyncio.sleep(0.2)
        reply = "Всегда пожалуйста! Если понадобится найти другие места, узнать режим работы или контакты — обращайтесь! 😊"
        return reply, []

    # 1. Check for Place Details request by ID or Name
    m_id = re.search(r"\b(?:id|ид)\s*[:#№]?\s*(\d+)\b", query, re.IGNORECASE)
    if not m_id:
        m_name = re.search(
            r"(?:подробнее\s+про|расскажи\s+про|телефон|часы\s+работы|контакты)\s+(?:заведение|кафе|ресторан|бар)?\s*([«\"][^»\"]+[»\"]|[A-Za-zА-Яа-яЁё0-9\-]{3,})",
            query,
            re.IGNORECASE,
        )
        if m_name:
            cand_name = m_name.group(1).strip("«»\"' ")
            rows_cand = query_poi("SELECT id FROM poi WHERE name LIKE ? LIMIT 1", (f"%{cand_name}%",))
            if rows_cand:
                m_id = type("Match", (), {"group": lambda self, x: str(rows_cand[0]["id"])})()

    if m_id:
        place_id = int(m_id.group(1))

        # Step 1: Thinking
        step1 = {
            "step": len(thoughts) + 1,
            "title": "Запрос информации о заведении",
            "detail": f"Обнаружен идентификатор заведения ID {place_id}. Запрашиваю точные данные из базы...",
        }
        thoughts.append(step1)
        await _safe_put(queue, {"event": "thinking", "data": step1})
        await asyncio.sleep(0.3)

        # Step 2: Tool get_place_details
        step2 = {
            "step": len(thoughts) + 1,
            "title": "Вызов инструмента: get_place_details",
            "detail": f"Получение подробной карточки места ID {place_id}...",
            "tool": "get_place_details",
            "args": {"place_id": place_id},
        }
        thoughts.append(step2)
        await _safe_put(queue, {"event": "tool_call", "data": {"tool": "get_place_details", "thought": step2}})
        await asyncio.sleep(0.4)

        details_str = do_get_place_details(place_id)
        step3 = {
            "step": len(thoughts) + 1,
            "title": "Результат: get_place_details",
            "detail": details_str[:200] + "...",
            "tool": "get_place_details",
            "result": details_str,
        }
        thoughts.append(step3)
        await _safe_put(queue, {"event": "tool_result", "data": {"tool": "get_place_details", "preview": details_str[:200], "thought": step3}})
        await asyncio.sleep(0.3)

        rows = query_poi("SELECT * FROM poi WHERE id = ?", (place_id,))
        if rows:
            p = dict(rows[0])
            name = p.get("name") or p.get("brand") or "Заведение"
            kind = p.get("kind") or "заведение"
            street = p.get("street")
            house = f", д. {p.get('housenumber')}" if p.get("housenumber") else ""
            if street:
                st = street.strip()
                if not re.match(r"^(улица|ул\.?|проспект|просп\.?|пр-кт|переулок|пер\.?|бульвар|площадь)", st, re.I):
                    st = f"ул. {st}"
                addr = f"{st}{house}"
            else:
                addr = "Ростов-на-Дону"
            hours = p.get("opening_hours") or "в базе OpenStreetMap не указаны (рекомендуется уточнить на месте)"
            is_24 = " • **Круглосуточно**" if p.get("is_24_7") else ""
            phone = p.get("phone") or "не указан"
            website = f"[{p.get('website')}]({p.get('website')})" if p.get("website") else "не указан"
            cuisine = f" • Кухня: *{p.get('cuisine')}*" if p.get("cuisine") else ""
            lat = p.get("lat")
            lon = p.get("lon")

            lines = [
                f"### 📍 Информация о заведении **«{name}»** (ID: {place_id})\n",
                f"- **Категория:** {kind}{cuisine}",
                f"- **Адрес:** {addr}",
                f"- **Часы работы:** {hours}{is_24}",
                f"- **Телефон:** {phone}",
                f"- **Сайт:** {website}",
                f"- **Координаты:** {lat:.5f}, {lon:.5f}" if lat and lon else "",
                "\n💬 **Отзывы посетителей:**",
                f"В открытой картографической базе OpenStreetMap текстовые отзывы не хранятся. Вы можете прочитать отзывы и посмотреть рейтинг заведения **«{name}»** на сервисах **Яндекс.Карты** или **2ГИС**.",
            ]
            content = "\n".join([line for line in lines if line])
            places = current_discovered_places.get()
            return content, places

    # 1. Routing / "Как добраться"
    if any(w in query.lower() for w in ["как добраться", "маршрут", "сколько до него метров", "сколько метров от центра"]):
        CENTER_LAT, CENTER_LON = 47.2225, 39.7187
        m_dest = re.search(r"(?:до|в)\s+([^,\(]+)", query, re.I)
        dest_name = m_dest.group(1).strip() if m_dest else "заведения"

        step1 = {
            "step": len(thoughts) + 1,
            "title": "Расчет маршрута",
            "detail": f"Определяю расстояние от центра города до '{dest_name}'...",
        }
        thoughts.append(step1)
        await _safe_put(queue, {"event": "thinking", "data": step1})
        await asyncio.sleep(0.3)

        rows = query_poi(
            "SELECT id, name, kind, street, housenumber, lat, lon FROM poi WHERE name LIKE ? OR street LIKE ? LIMIT 1",
            (f"%{dest_name}%", f"%{dest_name}%"),
        )
        if rows:
            p = dict(rows[0])
            dist = distance_m(CENTER_LAT, CENTER_LON, p["lat"], p["lon"])
            walk_min = max(2, round(dist / 75))
            text = (
                f"🚶 **Как добраться до «{p.get('name') or dest_name}»**:\n\n"
                f"- **Адрес:** ул. {p.get('street') or '—'} {p.get('housenumber') or ''}\n"
                f"- **Расстояние от центра** (парк Горького / Большая Садовая): примерно **{dist} м**.\n"
                f"- **Пешком:** около **{walk_min} минут** неспешным шагом.\n\n"
                f"💡 Маршрут: двигайтесь по Ворошиловскому или Будённовскому проспекту в сторону улицы {p.get('street') or 'назначения'}."
            )
            return text, current_discovered_places.get()

    # 2. Address proximity pattern (e.g. 'Вкусно поесть рядом с адресом Буденновский проспект, 30')
    street = None
    house = None

    # Pattern A: Address with house number (e.g. 'рядом с адресом Буденновский проспект, 30', 'около Пушкинской 44')
    m_addr_house = re.search(
        r"(?:рядом\s+с|недалеко\s+от|поблизости\s+от|около|возле|по\s+адресу|в\s+районе|на)\s+(?:адрес[а-я]*\s+)?([А-Яа-яЁёA-Za-z0-9\s\-]+?)(?:,\s*|\s+)(?:д(?:ом|\.)?\s*)?(\d+[\w\d/]*)(?:[,\s]|$)",
        query,
        re.IGNORECASE,
    )
    if m_addr_house:
        street = m_addr_house.group(1).strip()
        house = m_addr_house.group(2).strip()
    else:
        # Pattern B: Street name without house number
        m_addr_only = re.search(
            r"(?:рядом\s+с|недалеко\s+от|поблизости\s+от|около|возле|по\s+адресу|в\s+районе|на)\s+(?:адрес[а-я]*\s+)?([А-Яа-яЁёA-Za-z0-9\s\-]+(?:улиц[а-я]*|ул\.?|проспект[а-я]*|просп\.?|пр-кт|пр\.?|переул[а-я]*|пер\.?|бульвар[а-я]*|площад[а-я]*|[а-я]{2,3}ской|[а-я]{2,3}ском|[а-я]{2,3}ская|[а-я]{2,3}ский))",
            query,
            re.IGNORECASE,
        )
        if m_addr_only:
            street = m_addr_only.group(1).strip()
            house = None

    if street:
        # Step 1: Planning
        step1 = {
            "step": len(thoughts) + 1,
            "title": "Анализ гео-запроса",
            "detail": f"Выделен адрес '{street} {house or ''}'. Запускаю геокодер для определения координат...",
        }
        thoughts.append(step1)
        await _safe_put(queue, {"event": "thinking", "data": step1})
        await asyncio.sleep(0.3)

        # Step 2: Tool geocode_address
        step2 = {
            "step": len(thoughts) + 1,
            "title": "Вызов инструмента: geocode_address",
            "detail": f"Геокодирование адреса '{street}', дом '{house or 'не указан'}'...",
            "tool": "geocode_address",
            "args": {"street": street, "housenumber": house},
        }
        thoughts.append(step2)
        await _safe_put(queue, {"event": "tool_call", "data": {"tool": "geocode_address", "thought": step2}})
        await asyncio.sleep(0.4)

        geo_res = do_geocode_address(street, house)
        step3 = {
            "step": len(thoughts) + 1,
            "title": "Результат: geocode_address",
            "detail": geo_res,
            "tool": "geocode_address",
            "result": geo_res,
        }
        thoughts.append(step3)
        await _safe_put(queue, {"event": "tool_result", "data": {"tool": "geocode_address", "preview": geo_res, "thought": step3}})
        await asyncio.sleep(0.3)

        # Extract coordinates
        m_coords = re.search(r"lat=([\d\.]+),\s*lon=([\d\.]+)", geo_res)
        if m_coords:
            lat = float(m_coords.group(1))
            lon = float(m_coords.group(2))

            # Step 4: Tool find_nearest_places
            step4 = {
                "step": len(thoughts) + 1,
                "title": "Вызов инструмента: find_nearest_places",
                "detail": f"Поиск заведений питания в шаговой доступности вокруг координат ({lat:.4f}, {lon:.4f})...",
                "tool": "find_nearest_places",
                "args": {"lat": lat, "lon": lon, "kind": "food", "limit": 5},
            }
            thoughts.append(step4)
            await _safe_put(queue, {"event": "tool_call", "data": {"tool": "find_nearest_places", "thought": step4}})
            await asyncio.sleep(0.4)

            do_find_nearest_places(lat, lon, kind="food", limit=5)
            places = current_discovered_places.get()

            step5 = {
                "step": len(thoughts) + 1,
                "title": "Результат: find_nearest_places",
                "detail": f"Найдено {len(places)} заведений питания поблизости с точным расстоянием в метрах.",
                "tool": "find_nearest_places",
                "result": f"{len(places)} мест",
            }
            thoughts.append(step5)
            await _safe_put(queue, {"event": "tool_result", "data": {"tool": "find_nearest_places", "preview": f"Найдено {len(places)} мест", "thought": step5}})
            await asyncio.sleep(0.3)

            from app.agent.tools import format_address
            lines = [f"Рядом с адресом **{street}{f', д. {house}' if house else ''}** я нашёл заведения питания в шаговой доступности:\n"]
            for i, p in enumerate(places, 1):
                name = p.get("name") or "Без названия"
                kind = p.get("kind") or "заведение"
                dist = f" ({p.get('distance_m', 0)} м)" if "distance_m" in p else ""
                cuisine = f" • Кухня: *{p.get('cuisine')}*" if p.get("cuisine") else ""
                addr = format_address(p.get("street"), p.get("housenumber"))
                hours = p.get("opening_hours") or "уточняйте"
                is_24_7 = " • **Круглосуточно**" if p.get("is_24_7") else ""
                lines.append(f"{i}. **{name}** ({kind}){dist}\n   📍 {addr}\n   🕒 Часы: {hours}{is_24_7}{cuisine}\n")
            lines.append("\nВсе найденные заведения находятся в шаговой доступности от указанного адреса!")
            return "\n".join(lines), places

    # 3. Street search (e.g. 'кафе на Пушкинской')
    street_match = re.search(r"(?:на|по)\s+(?:ул(?:ице|\.)?\s+)?([А-Яа-яЁё]+(?:ская|ской|ского|ский| переулке| проспекте))", query, re.I)
    if street_match:
        street_cand = street_match.group(1)
        step1 = {
            "step": len(thoughts) + 1,
            "title": f"Поиск заведений на улице {street_cand}",
            "detail": "Запрос к базе данных города...",
            "tool": "find_places",
        }
        thoughts.append(step1)
        await _safe_put(queue, {"event": "tool_call", "data": {"tool": "find_places", "thought": step1}})
        await asyncio.sleep(0.3)
        do_find_places("cafe", street_cand, limit=5)
        places = current_discovered_places.get()
        if places:
            from app.agent.tools import format_address
            lines = [f"На улице **{street_cand}** найдены следующие заведения:\n"]
            for i, p in enumerate(places, 1):
                name = p.get("name") or "Без названия"
                kind = p.get("kind") or "место"
                addr = format_address(p.get("street"), p.get("housenumber"))
                hours = p.get("opening_hours") or "уточняйте"
                lines.append(f"{i}. **{name}** ({kind})\n   📍 {addr}\n   🕒 Часы: {hours}\n")
            return "\n".join(lines), places

    # 4. Semantic search (e.g. 'пельмени', 'кофе', 'хинкали')
    step1 = {
        "step": len(thoughts) + 1,
        "title": "Семантический векторный анализ",
        "detail": f"Поиск в векторной базе ChromaDB по смыслу запроса: '{query}'",
        "tool": "semantic_search",
    }
    thoughts.append(step1)
    await _safe_put(queue, {"event": "tool_call", "data": {"tool": "semantic_search", "thought": step1}})
    await asyncio.sleep(0.4)

    from app.vector.indexer import search_semantic
    from app.agent.tools import format_address, enrich_place_address
    places = search_semantic(query, limit=5)

    step2 = {
        "step": len(thoughts) + 1,
        "title": "Результаты семантического сопоставления",
        "detail": f"Найдено {len(places)} релевантных заведений по смыслу.",
        "tool": "semantic_search",
        "result": f"{len(places)} мест",
    }
    thoughts.append(step2)
    await _safe_put(queue, {"event": "tool_result", "data": {"tool": "semantic_search", "preview": f"Найдено {len(places)} мест", "thought": step2}})
    await asyncio.sleep(0.3)

    if not places:
        return f"К сожалению, по запросу '{query}' не удалось найти подходящих заведений в базе Ростова-на-Дону.", []

    lines = [f"По вашему запросу **«{query}»** я нашёл наиболее подходящие заведения в Ростове-на-Дону:\n"]
    for i, p in enumerate(places, 1):
        enrich_place_address(p)
        name = p.get("name") or "Без названия"
        kind = p.get("kind") or "заведение"
        cuisine = f" • Кухня: *{p.get('cuisine')}*" if p.get("cuisine") else ""
        addr = format_address(p.get("street"), p.get("housenumber"))
        hours = p.get("opening_hours") or "уточняйте"
        is_24_7 = " • **Круглосуточно**" if p.get("is_24_7") else ""
        lines.append(f"{i}. **{name}** ({kind})\n   📍 {addr}\n   🕒 Часы: {hours}{is_24_7}{cuisine}\n")
    lines.append("\nВы можете спросить точный маршрут, контакты или режим работы!")
    return "\n".join(lines), places


async def run_fallback_search(
    query: str,
    queue: asyncio.Queue,
    thoughts: list[dict[str, Any]],
    history: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    return await run_local_agent(query, queue, thoughts, history=history)


async def run_mock_agent(
    query: str,
    queue: asyncio.Queue,
    thoughts: list[dict[str, Any]],
    history: list[dict[str, Any]] | None = None,
) -> None:
    text, places = await run_local_agent(query, queue, thoughts, history=history)
    await _safe_put(queue, {"event": "token", "data": {"text": text}})
    if places:
        await _safe_put(queue, {"event": "places", "data": places})

    await _safe_put(queue, {
        "event": "done",
        "data": {
            "content": text,
            "thoughts": thoughts,
            "places": places,
        },
    })
