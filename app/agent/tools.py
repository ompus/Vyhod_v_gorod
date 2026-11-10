from __future__ import annotations

import contextvars
import math
import re
import sqlite3
from typing import Any
def function_tool(func=None, **kwargs):
    """Декоратор-заглушка для совместимости: функции вызываются напрямую."""
    if func is not None:
        return func
    def decorator(f):
        return f
    return decorator

from app.config import POI_DB_PATH
from app.vector.indexer import search_semantic

# Context variable to collect place cards found during a request run
current_discovered_places: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "current_discovered_places", default=[]
)

LAT_M = 111_320
LON_M = LAT_M * math.cos(math.radians(47.23))


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    return int(math.hypot((lat1 - lat2) * LAT_M, (lon1 - lon2) * LON_M))


def query_poi(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with sqlite3.connect(POI_DB_PATH) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, params)]


def format_address(street: str | None, housenumber: str | None) -> str:
    if not street or str(street).strip() in ("", "—", "None"):
        return "Ростов-на-Дону (центр)"
    st = str(street).strip()
    if not re.search(r"\b(улица|ул\.?|проспект|просп\.?|пр-кт|пр\.?|переулок|пер\.?|бульвар|площадь|шоссе|набережная)\b", st, re.I):
        st = f"ул. {st}"
    house = f", д. {housenumber}" if housenumber and str(housenumber).strip() not in ("None", "", "—") else ""
    return f"{st}{house}"


def enrich_place_address(place: dict[str, Any]) -> dict[str, Any]:
    """Reverse geocode missing street/housenumber from nearest building POI in SQLite."""
    street = place.get("street")
    if not street or str(street).strip() in ("None", "", "—"):
        lat = place.get("lat")
        lon = place.get("lon")
        if lat and lon:
            rows = query_poi(
                "SELECT street, housenumber, lat, lon FROM poi "
                "WHERE street IS NOT NULL AND street != '' AND street != 'None' "
                "AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? LIMIT 20",
                (lat - 0.002, lat + 0.002, lon - 0.002, lon + 0.002),
            )
            if rows:
                nearest = min(rows, key=lambda r: distance_m(lat, lon, r["lat"], r["lon"]))
                if distance_m(lat, lon, nearest["lat"], nearest["lon"]) <= 180:
                    place["street"] = nearest["street"]
                    if not place.get("housenumber"):
                        place["housenumber"] = nearest["housenumber"]
    return place


def _record_place(place: dict[str, Any]) -> None:
    try:
        enrich_place_address(place)
        places_list = current_discovered_places.get()
        if not any(p.get("id") == place.get("id") for p in places_list):
            places_list.append(place)
    except Exception:
        pass


def do_semantic_search(query: str, limit: int = 5) -> str:
    results = search_semantic(query, limit=limit)
    if not results:
        return f"По запросу '{query}' ничего не нашлось в векторной базе."

    lines = []
    for r in results:
        _record_place(r)
        name = r.get("name") or "Без названия"
        kind = r.get("kind") or ""
        cuisine = f" (кухня: {r.get('cuisine')})" if r.get("cuisine") else ""
        addr = format_address(r.get("street"), r.get("housenumber"))
        hours = f", часы: {r.get('opening_hours')}" if r.get("opening_hours") else ""
        is_24_7 = " [Круглосуточно]" if r.get("is_24_7") else ""
        lines.append(
            f"• ID {r.get('id')}: {name} ({kind}){cuisine} | {addr}{hours}{is_24_7}"
        )

    return "\n".join(lines)


@function_tool
def semantic_search(query: str, limit: int = 5) -> str:
    """Семантический векторный поиск мест по смыслу, блюдам, атмосфере или типу.
    
    Зови обязательно, когда:
    - Пользователь забыл точное название заведения и описывает блюда или атмосферу
      (например: «пельмени», «хинкали», «где поесть пасту», «уютная кофейня с десертами», «грузинская еда»).
    - Пользователь ищет место по ассоциациям или неформальному описанию.
    ВНИМАНИЕ: НЕ вызывай для поиска заведений рядом с конкретным адресом (для этого используй geocode_address + find_nearest_places)!

    Args:
        query: поисковая фраза (например: 'пельмени', 'кофе и круассаны', 'сытный обед').
        limit: количество результатов (по умолчанию 5).
    """
    return do_semantic_search(query, limit)


def _normalize_street_stem(street: str) -> str:
    clean_street = re.sub(
        r"\b(улица|ул\.?|проспект|просп\.?|пр-кт|пр\.?|переулок|пер\.?|бульвар|бульв\.?|площадь|пл\.?|город|г\.)\b",
        "",
        street,
        flags=re.IGNORECASE,
    ).strip()

    words = [w for w in re.findall(r"[А-Яа-яЁёA-Za-z]{3,}", clean_street)]
    stop_stems = {"город", "ростов", "дон", "больш", "мал"}
    cand_words = [w for w in words if w.lower()[:5] not in stop_stems] or words

    if cand_words:
        word = max(cand_words, key=len)
        # Strip Russian adjective/noun endings
        stem = re.sub(
            r"(ский|ской|ском|ская|скую|ского|ому|ыми|ами|ями|ов|ев|ая|ое|ий|ый|ой|ом|ем|а|я|у|ю|е|ы|и)$",
            "",
            word,
            flags=re.IGNORECASE,
        )
        if len(stem) < 3:
            stem = word[:4]
    else:
        stem = clean_street[:5] if clean_street else street

    # Replace е/ё and first letter with '_' wildcard so SQLite LIKE is case- and ё-insensitive
    sql_stem = re.sub(r"[еёЕЁ]", "_", stem)
    if len(sql_stem) > 2:
        sql_stem = "_" + sql_stem[1:]
    return sql_stem


def do_find_places(kind: str, street: str | None = None, limit: int = 5) -> str:
    sql = "SELECT id, name, brand, kind, cuisine, street, housenumber, opening_hours, is_24_7, lat, lon FROM poi WHERE kind = ?"
    params: list[Any] = [kind]
    if street:
        sql_stem = _normalize_street_stem(street)
        sql += " AND street LIKE ?"
        params.append(f"%{sql_stem}%")
    sql += " LIMIT ?"
    params.append(limit)

    rows = query_poi(sql, tuple(params))
    if not rows:
        return f"Мест вида '{kind}'" + (f" на улице '{street}'" if street else "") + " не найдено."

    lines = []
    for r in rows:
        _record_place(r)
        name = r.get("name") or r.get("brand") or "Без названия"
        addr = format_address(r.get("street"), r.get("housenumber"))
        hours = r.get("opening_hours") or "не указаны"
        lines.append(f"• ID {r['id']}: {name} | {addr} | часы: {hours}")

    return "\n".join(lines)


@function_tool
def find_places(kind: str, street: str | None = None, limit: int = 5) -> str:
    """Точный поиск заведений заданного вида в базе города, опционально на конкретной улице.

    Args:
        kind: вид места латиницей (pharmacy, cafe, restaurant, supermarket, outpost, bank, school).
        street: часть названия улицы; если None — поиск по всему городу.
        limit: сколько заведений вернуть (по умолчанию 5).
    """
    return do_find_places(kind, street, limit)


def do_get_place_details(place_id: int) -> str:
    rows = query_poi(
        "SELECT id, name, brand, kind, cuisine, street, housenumber, opening_hours, "
        "is_24_7, phone, website, lat, lon FROM poi WHERE id = ?",
        (place_id,),
    )
    if not rows:
        return f"Место с ID {place_id} не найдено в базе."

    r = rows[0]
    _record_place(r)
    addr = format_address(r.get("street"), r.get("housenumber"))
    return (
        f"Карточка места ID {r['id']}:\n"
        f"- Название: {r.get('name') or r.get('brand') or 'Без названия'}\n"
        f"- Категория: {r.get('kind')}\n"
        f"- Кухня: {r.get('cuisine') or 'не указана'}\n"
        f"- Адрес: {addr}\n"
        f"- Часы работы: {r.get('opening_hours') or 'не указаны'}\n"
        f"- Круглосуточно: {'Да' if r.get('is_24_7') else 'Нет'}\n"
        f"- Телефон: {r.get('phone') or 'не указан'}\n"
        f"- Сайт: {r.get('website') or 'не указан'}\n"
        f"- Координаты: lat={r.get('lat')}, lon={r.get('lon')}"
    )


@function_tool
def get_place_details(place_id: int) -> str:
    """Подробная карточка места: точный адрес, телефон, часы работы, сайт и координаты."""
    return do_get_place_details(place_id)


def do_geocode_address(street: str, housenumber: str | None = None) -> str:
    sql_stem = _normalize_street_stem(street)

    # 1. Exact or prefix match with housenumber (e.g. '30' matches '30' or '30/46' or '30А')
    if housenumber:
        clean_house = re.sub(r"[^\w\d/]", "", str(housenumber)).strip()
        rows = query_poi(
            "SELECT id, street, housenumber, lat, lon FROM poi "
            "WHERE street LIKE ? AND (housenumber = ? OR housenumber LIKE ? OR housenumber LIKE ?) LIMIT 1",
            (f"%{sql_stem}%", clean_house, f"{clean_house}/%", f"{clean_house}%"),
        )
        if rows:
            r = rows[0]
            return f"Найден точный адрес: {r['street']}, д. {r['housenumber']}, lat={r['lat']}, lon={r['lon']}"

    # 2. Fallback on street
    rows = query_poi(
        "SELECT id, street, housenumber, lat, lon FROM poi WHERE street LIKE ? ORDER BY housenumber IS NOT NULL DESC LIMIT 1",
        (f"%{sql_stem}%",),
    )
    if rows:
        r = rows[0]
        near_desc = f" (ориентир: д. {r['housenumber']})" if r.get('housenumber') else ""
        return f"Найден адрес: {r['street']}{near_desc}, lat={r['lat']}, lon={r['lon']}"

    return f"Адрес '{street} {housenumber or ''}' не найден в базе данных Ростова-на-Дону."


@function_tool
def geocode_address(street: str, housenumber: str | None = None) -> str:
    """Определение координат дома или улицы в Ростове-на-Дону.

    Зови, когда нужно узнать координаты адреса пользователя для поиска ближайших мест.

    Args:
        street: название улицы (например: 'Буденновский проспект', 'Города Волос', 'Пушкинская').
        housenumber: номер дома (например: '30', '8', '44').
    """
    return do_geocode_address(street, housenumber)


def do_find_nearest_places(
    lat: float,
    lon: float,
    kind: str = "food",
    query: str | None = None,
    limit: int = 5,
) -> str:
    if kind == "food":
        sql = (
            "SELECT id, name, brand, kind, cuisine, street, housenumber, opening_hours, is_24_7, lat, lon "
            "FROM poi WHERE kind IN ('cafe', 'restaurant', 'fast_food', 'bakery', 'bar') "
            "AND name IS NOT NULL"
        )
        rows = query_poi(sql)
    else:
        sql = (
            "SELECT id, name, brand, kind, cuisine, street, housenumber, opening_hours, is_24_7, lat, lon "
            "FROM poi WHERE kind = ?"
        )
        rows = query_poi(sql, (kind,))

    if not rows:
        return f"Мест вида '{kind}' не найдено поблизости."

    for r in rows:
        r["distance_m"] = distance_m(lat, lon, r["lat"], r["lon"])

    # If query is specified (e.g. 'пельмени', 'пицца', 'бургеры'), search among places nearby
    matched_query_places = []
    if query:
        q_lower = query.lower().strip()
        q_words = [w for w in re.findall(r"[а-яёa-z]{3,}", q_lower)]
        for r in rows:
            # Look only in a reasonable walking/short drive radius (up to 2000m)
            if r["distance_m"] > 2000:
                continue
            searchable = f"{r.get('name') or ''} {r.get('brand') or ''} {r.get('cuisine') or ''}".lower()
            if any(w in searchable for w in q_words) or q_lower in searchable:
                matched_query_places.append(r)

    lines = []
    if query and matched_query_places:
        nearest = sorted(matched_query_places, key=lambda x: x["distance_m"])[:limit]
        lines.append(f"Заведения с '{query}' поблизости (в радиусе до 2 км):")
    elif query:
        # Fallback to nearest general places
        nearest = sorted(rows, key=lambda x: x["distance_m"])[:limit]
        lines.append(f"Специализированных заведений с блюдом/кухней '{query}' в радиусе до 2 км не найдено. Вот ближайшие места питания:")
    else:
        nearest = sorted(rows, key=lambda x: x["distance_m"])[:limit]

    for r in nearest:
        _record_place(r)
        name = r.get("name") or r.get("brand") or "Без названия"
        dist = r["distance_m"]
        addr = format_address(r.get("street"), r.get("housenumber"))
        cuisine = f" • кухня: {r.get('cuisine')}" if r.get('cuisine') else ""
        hours = f" • часы: {r.get('opening_hours')}" if r.get('opening_hours') else ""
        is_24_7 = " • [Круглосуточно]" if r.get('is_24_7') else ""
        lines.append(f"• ID {r['id']}: {name} ({r.get('kind')}) — {dist} м | {addr}{cuisine}{hours}{is_24_7}")

    return "\n".join(lines)


@function_tool
def find_nearest_places(
    lat: float,
    lon: float,
    kind: str = "food",
    query: str | None = None,
    limit: int = 5,
) -> str:
    """Поиск ближайших заведений к указанным географическим координатам.

    Args:
        lat: широта точки.
        lon: долгота точки.
        kind: вид места: 'food' (кафе, рестораны, фастфуд, пекарни), 'cafe', 'restaurant', 'fast_food', 'bar', 'pharmacy', 'supermarket'.
        query: опциональное блюдо или кухня для фильтрации среди ближайших мест (например: 'пельмени', 'пицца', 'бургеры', 'суши', 'кофе').
        limit: количество заведений (по умолчанию 5).
    """
    return do_find_nearest_places(lat, lon, kind, query, limit)


ALL_AGENT_TOOLS = [
    semantic_search,
    find_places,
    get_place_details,
    geocode_address,
    find_nearest_places,
]
