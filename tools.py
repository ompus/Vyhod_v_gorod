from __future__ import annotations

import collections
import contextvars
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

LAT_M = 111_320                                  # метров в градусе широты
LON_M = LAT_M * math.cos(math.radians(47.23))    # метров в градусе долготы в Ростове

# Допустимые типы мест из базы OSM Ростова-на-Дону
VALID_KINDS = {
    "pharmacy", "cafe", "restaurant", "fast_food", "supermarket",
    "convenience", "outpost", "bank", "school", "kindergarten",
    "playground", "park", "fuel", "hospital", "cinema", "bar",
    "bakery", "beauty", "atm"
}

# Контекстная переменная для сбора карточек мест в течение одного запроса (для веб-интерфейса)
current_discovered_places: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "current_discovered_places", default=[]
)

# Поиск базы poi.sqlite в стандартных расположениях
_CANDIDATE_PATHS = [
    Path("data/poi.sqlite").resolve()
]

DB_PATH: Path | None = None
for p in _CANDIDATE_PATHS:
    if p.exists():
        DB_PATH = p
        break

if DB_PATH is None:
    DB_PATH = _CANDIDATE_PATHS[0]

def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    return int(math.hypot((lat1 - lat2) * LAT_M, (lon1 - lon2) * LON_M))


def _format_table(items: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    if not items:
        return "Ничего не найдено."
    head = " | ".join(columns)
    sep = "-+-".join("-" * len(c) for c in columns)
    body = "\n".join(
        " | ".join(str(it.get(c) if it.get(c) is not None else "—") for c in columns)
        for it in items
    )
    return f"{head}\n{sep}\n{body}"

def enrich_place_address(place: dict[str, Any]) -> dict[str, Any]:
    """Обогащает пропущенный адрес по ближайшему зданию с известным адресом в SQLite."""
    street = place.get("street")
    if not street or str(street).strip() in ("None", "", "—"):
        lat = place.get("lat")
        lon = place.get("lon")
        if lat and lon:
            rows = _query(
                "SELECT street, housenumber, lat, lon FROM poi "
                "WHERE street IS NOT NULL AND street != '' AND street != 'None' "
                "AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? LIMIT 20",
                (lat - 0.003, lat + 0.003, lon - 0.003, lon + 0.003),
            )
            if rows:
                nearest = min(rows, key=lambda r: _distance_m(lat, lon, r["lat"], r["lon"]))
                if _distance_m(lat, lon, nearest["lat"], nearest["lon"]) <= 180:
                    place["street"] = nearest["street"]
                    if not place.get("housenumber"):
                        place["housenumber"] = nearest["housenumber"]
    return place

def _record_place(place: dict[str, Any]) -> None:
    try:
        enrich_place_address(place)
        places_list = current_discovered_places.get()
        if not any(p.get("id") == place.get("id") for p in places_list):
            places_list.append(dict(place))
    except Exception:
        pass

def _query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    if not DB_PATH or not DB_PATH.exists():
        raise FileNotFoundError(f"Файл базы данных poi.sqlite не найден: {DB_PATH}")
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, params)]

def _clean_kind(kind: str) -> str:
    k = str(kind).strip().lower()
    mapping = {
        "аптека": "pharmacy",
        "аптеки": "pharmacy",
        "кино": "cinema",
        "кинотеатр": "cinema",
        "кинотеатры": "cinema",
        "кафе": "cafe",
        "ресторан": "restaurant",
        "рестораны": "restaurant",
        "фастфуд": "fast_food",
        "супермаркет": "supermarket",
        "супермаркеты": "supermarket",
        "магазин": "convenience",
        "магазины": "convenience",
        "пункт выдачи": "outpost",
        "пункты выдачи": "outpost",
        "пвз": "outpost",
        "банк": "bank",
        "банки": "bank",
        "школа": "school",
        "школы": "school",
        "детский сад": "kindergarten",
        "детские сады": "kindergarten",
        "заправка": "fuel",
        "заправки": "fuel",
        "азс": "fuel",
        "парк": "park",
        "парки": "park",
        "больница": "hospital",
        "больницы": "hospital",
        "бар": "bar",
        "бары": "bar",
    }
    return mapping.get(k, k)

# Инструменты

def count_places(kind: str, only_24_7: bool = False) -> str:
    """Подсчет количества мест заданного типа."""
    clean_k = _clean_kind(kind)
    if clean_k not in VALID_KINDS:
        raise ValueError(
            f"Недопустимый вид места kind='{kind}'. Укажите латиницей: {', '.join(sorted(VALID_KINDS))}"
        )

    sql = "SELECT COUNT(*) AS n FROM poi WHERE kind = ?"
    params: list[Any] = [clean_k]
    if only_24_7:
        sql += " AND is_24_7 = 1"

    res = _query(sql, tuple(params))
    count = res[0]["n"] if res else 0
    mod = " (круглосуточных)" if only_24_7 else ""
    return f"{clean_k}{mod}: {count} шт."


def find_places(kind: str, street: str | None = None, limit: int = 5) -> str:
    """Список мест заданного вида в городе или на конкретной улице."""
    clean_k = _clean_kind(kind)
    if clean_k not in VALID_KINDS:
        raise ValueError(
            f"Недопустимый вид места kind='{kind}'. Укажите латиницей: {', '.join(sorted(VALID_KINDS))}"
        )

    sql = "SELECT id, name, street, housenumber, opening_hours, lat, lon FROM poi WHERE kind = ?"
    params: list[Any] = [clean_k]

    stem = None
    if street:
        words = [w for w in re.findall(r"[А-Яа-яЁёA-Za-z]{3,}", street)
                 if not re.match(r"^(улиц|проспек|переул|бульвар|площад)", w.lower())]
        stem = max(words, key=len)[:-1] if words else street.strip()
        sql += " AND street LIKE ?"
        params.append(f"%{stem}%")
    else:
        sql += " ORDER BY (street IS NOT NULL AND street != '') DESC, (name IS NOT NULL AND name != '') DESC"

    sql += " LIMIT ?"
    params.append(min(max(1, int(limit)), 20))

    got = _query(sql, tuple(params))

    if street and stem and len(got) < limit:
        r_st = _query(
            "SELECT lat, lon FROM poi WHERE street LIKE ? LIMIT 1",
            (f"%{stem}%",),
        )
        if r_st:
            st_lat, st_lon = r_st[0]["lat"], r_st[0]["lon"]
            all_places = _query(
                "SELECT id, name, street, housenumber, opening_hours, lat, lon FROM poi WHERE kind = ?",
                (clean_k,),
            )
            existing_ids = {r["id"] for r in got}
            candidates = []
            for p in all_places:
                if p["id"] not in existing_ids and p.get("lat") and p.get("lon"):
                    d = _distance_m(st_lat, st_lon, p["lat"], p["lon"])
                    if d <= 450:
                        candidates.append((d, p))
            candidates.sort(key=lambda x: x[0])
            for _, p in candidates[:limit - len(got)]:
                got.append(p)

    for r in got:
        enrich_place_address(r)
        _record_place(r)
    return _format_table(got, ("id", "name", "street", "housenumber", "opening_hours"))


def top_brands(kind: str, limit: int = 5) -> str:
    """Рейтинг сетей и брендов для заданного вида заведений."""
    clean_k = _clean_kind(kind)
    if clean_k not in VALID_KINDS:
        raise ValueError(
            f"Недопустимый вид места kind='{kind}'. Укажите латиницей: {', '.join(sorted(VALID_KINDS))}"
        )

    sql = (
        "SELECT COALESCE(brand, 'без бренда') AS brand, COUNT(*) AS n "
        "FROM poi WHERE kind = ? GROUP BY 1 ORDER BY n DESC LIMIT ?"
    )
    got = _query(sql, (clean_k, min(max(1, int(limit)), 15)))
    return _format_table(got, ("brand", "n"))


def geocode_address(street: str, housenumber: str | None = None) -> str:
    """Определение координат дома или улицы по адресу."""
    if not street or not str(street).strip():
        raise ValueError("Название улицы не может быть пустым.")

    words = [w for w in re.findall(r"[А-Яа-яЁёA-Za-z]{3,}", street)
             if not re.match(r"^(улиц|проспек|переул|бульвар|площад)", w.lower())]
    stem = max(words, key=len)[:-1] if words else street.strip()

    if housenumber and str(housenumber).strip():
        clean_house = str(housenumber).strip()
        rows = _query(
            "SELECT id, name, street, housenumber, lat, lon FROM poi "
            "WHERE street LIKE ? AND housenumber = ? LIMIT 1",
            (f"%{stem}%", clean_house),
        )
        if rows:
            r = rows[0]
            return (
                f"Адрес найден: {r['street']}, д. {r['housenumber']}. "
                f"Координаты: lat={r['lat']:.6f}, lon={r['lon']:.6f}"
            )

    rows = _query(
        "SELECT id, name, street, housenumber, lat, lon FROM poi "
        "WHERE street LIKE ? ORDER BY housenumber IS NOT NULL DESC LIMIT 1",
        (f"%{stem}%",),
    )
    if rows:
        r = rows[0]
        house_info = f", д. {r['housenumber']}" if r.get("housenumber") else ""
        return (
            f"Улица найдена: {r['street']}{house_info}. "
            f"Координаты: lat={r['lat']:.6f}, lon={r['lon']:.6f}"
        )

    return f"Адрес '{street} {housenumber or ''}' не найден в базе данных Ростова-на-Дону."


def nearest_place(lat: float, lon: float, kind: str, limit: int = 1) -> str:
    """Поиск ближайших заведений заданного вида к географическим координатам."""
    clean_k = _clean_kind(kind)
    if clean_k not in VALID_KINDS:
        raise ValueError(
            f"Недопустимый вид места kind='{kind}'. Укажите латиницей: {', '.join(sorted(VALID_KINDS))}"
        )

    lat = float(lat)
    lon = float(lon)
    if not (46.0 <= lat <= 48.5 and 38.5 <= lon <= 41.0):
        raise ValueError(f"Координаты ({lat}, {lon}) находятся вне границ Ростова-на-Дону.")

    rows = _query(
        "SELECT id, name, street, housenumber, opening_hours, phone, lat, lon FROM poi WHERE kind = ?",
        (clean_k,),
    )
    if not rows:
        return f"Заведений вида '{clean_k}' не найдено в базе."

    for r in rows:
        r["distance_m"] = _distance_m(lat, lon, r["lat"], r["lon"])

    nearest = sorted(rows, key=lambda x: x["distance_m"])[:min(max(1, int(limit)), 10)]
    for r in nearest:
        _record_place(r)
    return _format_table(nearest, ("id", "name", "distance_m", "street", "housenumber", "opening_hours"))


def get_place_details(place_id: int) -> str:
    """Подробная карточка заведения: точный адрес, телефон, часы работы, сайт и координаты."""
    rows = _query(
        "SELECT id, name, brand, kind, cuisine, street, housenumber, opening_hours, "
        "is_24_7, phone, website, lat, lon FROM poi WHERE id = ?",
        (int(place_id),),
    )
    if not rows:
        return f"Место с ID {place_id} не найдено в базе."
    r = rows[0]
    _record_place(r)
    name = r.get("name") or r.get("brand") or "Без названия"
    kind = r.get("kind") or "место"
    cuisine = r.get("cuisine") or "не указана"
    st = r.get("street") or "Ростов-на-Дону"
    house = f", д. {r['housenumber']}" if r.get("housenumber") else ""
    hours = r.get("opening_hours") or "не указаны"
    is_24 = "Да" if r.get("is_24_7") else "Нет"
    phone = r.get("phone") or "не указан"
    web = r.get("website") or "не указан"
    lat, lon = r.get("lat"), r.get("lon")
    return (
        f"Карточка заведения ID {r['id']}:\n"
        f"- Название: {name}\n"
        f"- Категория: {kind}\n"
        f"- Кухня: {cuisine}\n"
        f"- Адрес: {st}{house}\n"
        f"- Часы работы: {hours}\n"
        f"- Круглосуточно: {is_24}\n"
        f"- Телефон: {phone}\n"
        f"- Сайт: {web}\n"
        f"- Координаты: lat={lat}, lon={lon}"
    )


def semantic_search(query: str, limit: int = 5) -> str:
    """Гибридный поиск мест: точные совпадения по названию/бренду в SQLite + семантика ChromaDB."""
    clean_q = query.strip()
    direct_results: list[dict[str, Any]] = []

    # 1. Сначала ищем прямое совпадение по названию заведения или бренду
    clean_terms = [clean_q]
    norm_term = re.sub(r"[\"—\-\s]+", "%", clean_q).strip("%")
    if norm_term and norm_term != clean_q:
        clean_terms.append(norm_term)

    for term in clean_terms:
        rows = _query(
            "SELECT id, name, brand, kind, cuisine, street, housenumber, opening_hours, is_24_7, lat, lon "
            "FROM poi WHERE (name LIKE ? OR brand LIKE ?) AND name IS NOT NULL LIMIT ?",
            (f"%{term}%", f"%{term}%", limit),
        )
        for r in rows:
            if not any(d["id"] == r["id"] for d in direct_results):
                direct_results.append(dict(r))
        if len(direct_results) >= limit:
            break

    # 2. Дополняем векторным поиском ChromaDB, если требуется
    chroma_results: list[dict[str, Any]] = []
    if len(direct_results) < limit:
        try:
            from app.vector.indexer import search_semantic as _search_chroma
            chroma_results = _search_chroma(query, limit=limit)
        except Exception:
            chroma_results = []

    results = list(direct_results)
    existing_ids = {r["id"] for r in results}
    for cr in chroma_results:
        if cr.get("id") not in existing_ids:
            results.append(cr)
            existing_ids.add(cr.get("id"))
        if len(results) >= limit:
            break

    if not results:
        return f"По запросу '{query}' ничего не найдено в базе заведений."

    lines = []
    for r in results:
        enrich_place_address(r)
        _record_place(r)
        name = r.get("name") or r.get("brand") or "Без названия"
        kind = r.get("kind") or ""
        cuisine = f" (кухня: {r.get('cuisine')})" if r.get("cuisine") else ""
        st = r.get("street") or "Ростов-на-Дону"
        house = f", д. {r['housenumber']}" if r.get("housenumber") else ""
        hours = f", часы: {r.get('opening_hours')}" if r.get("opening_hours") else ""
        is_24 = " [Круглосуточно]" if r.get("is_24_7") else ""
        lines.append(f"• ID {r.get('id')}: {name} ({kind}){cuisine} | {st}{house}{hours}{is_24}")

    return "\n".join(lines)


# Диспетчер вызова инструментов и LRU-кеш идемпотентных запросов

AVAILABLE_TOOLS = {
    "count_places": count_places,
    "find_places": find_places,
    "top_brands": top_brands,
    "geocode_address": geocode_address,
    "nearest_place": nearest_place,
    "semantic_search": semantic_search,
    "get_place_details": get_place_details,
}

_TOOL_CACHE: collections.OrderedDict[str, str] = collections.OrderedDict()
_CACHE_MAX_SIZE = 512
_CACHEABLE_TOOLS = {
    "count_places",
    "top_brands",
    "geocode_address",
    "nearest_place",
    "get_place_details",
    "find_places",
    "semantic_search",
}


def _make_cache_key(name: str, args: dict[str, Any]) -> str:
    """Формирует канонический ключ кеша на основе имени и отсортированных аргументов."""
    try:
        return f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
    except Exception:
        return f"{name}:{str(args)}"


def clear_tool_cache() -> None:
    """Очищает LRU-кеш инструментов."""
    _TOOL_CACHE.clear()


def get_cache_stats() -> dict[str, int]:
    """Возвращает статистику размера LRU-кеша."""
    return {"size": len(_TOOL_CACHE), "max_size": _CACHE_MAX_SIZE}


def execute_tool(name: str, args: dict[str, Any], use_cache: bool = True) -> str:
    """Вызов функции по имени с перехватом аргументов и LRU-кешированием."""
    if name not in AVAILABLE_TOOLS:
        raise KeyError(
            f"Инструмент '{name}' не зарегистрирован. Доступные: {list(AVAILABLE_TOOLS.keys())}"
        )

    cache_key = _make_cache_key(name, args) if use_cache and name in _CACHEABLE_TOOLS else None
    if cache_key and cache_key in _TOOL_CACHE:
        _TOOL_CACHE.move_to_end(cache_key)
        return _TOOL_CACHE[cache_key]

    func = AVAILABLE_TOOLS[name]
    result = str(func(**args))

    if cache_key:
        _TOOL_CACHE[cache_key] = result
        if len(_TOOL_CACHE) > _CACHE_MAX_SIZE:
            _TOOL_CACHE.popitem(last=False)

    return result

# Короткие схемы инструментов

TOOLS_SHORT = [
    {
        "type": "function",
        "function": {
            "name": "count_places",
            "description": "Возвращает количество мест заданного вида в городе.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Вид места латиницей."
                    },
                    "only_24_7": {
                        "type": "boolean",
                        "description": "Только круглосуточные."
                    }
                },
                "required": ["kind"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_places",
            "description": "Поиск заведений заданного вида в городе или на улице.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Вид места латиницей."
                    },
                    "street": {
                        "type": "string",
                        "description": "Название улицы."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Количество результатов."
                    }
                },
                "required": ["kind"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "top_brands",
            "description": "Рейтинг популярных брендов и сетей для заданного вида мест.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Вид места латиницей."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Количество позиций в рейтинге."
                    }
                },
                "required": ["kind"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "geocode_address",
            "description": "Определение координат по адресу дома или улицы в Ростове-на-Дону.",
            "parameters": {
                "type": "object",
                "properties": {
                    "street": {
                        "type": "string",
                        "description": "Название улицы."
                    },
                    "housenumber": {
                        "type": "string",
                        "description": "Номер дома."
                    }
                },
                "required": ["street"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "nearest_place",
            "description": "Поиск ближайших заведений к указанным координатам.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {
                        "type": "number",
                        "description": "Широта точки."
                    },
                    "lon": {
                        "type": "number",
                        "description": "Долгота точки."
                    },
                    "kind": {
                        "type": "string",
                        "description": "Вид заведения."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Количество заведений."
                    }
                },
                "required": ["lat", "lon", "kind"]
            }
        }
    }
]

# Полные схемы инструментов

TOOLS_FULL = [
    {
        "type": "function",
        "function": {
            "name": "count_places",
            "description": (
                "Подсчитывает общее число заведений заданного вида в Ростове-на-Дону. "
                "ИСПОЛЬЗУЙ, когда пользователь спрашивает «сколько», «какое количество», «число заведений». "
                "НЕ ИСПОЛЬЗУЙ, если нужен перечень мест, адреса или часы работы (для этого зови find_places), "
                "и не используй для поиска ближайших заведений (зови nearest_place). "
                "Единицы: возвращает целое число штук. "
                "Пример: count_places(kind='pharmacy', only_24_7=True) для круглосуточных аптек."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": (
                            "Вид места латиницей из базы OpenStreetMap. "
                            "Допустимые значения: pharmacy (аптека), cafe (кафе), restaurant (ресторан), "
                            "fast_food (быстрое питание), supermarket (супермаркет), convenience (магазин у дома), "
                            "outpost (пункт выдачи заказов), bank (банк), school (школа), kindergarten (детсад), "
                            "playground (детская площадка), park (парк), fuel (АЗС), hospital (больница)."
                        )
                    },
                    "only_24_7": {
                        "type": "boolean",
                        "description": "True — учитывать только круглосуточные заведения, False — все."
                    }
                },
                "required": ["kind"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_places",
            "description": (
                "Возвращает список мест заданного вида в Ростове-на-Дону с адресами и часами работы. "
                "ИСПОЛЬЗУЙ, когда нужен перечень, названия или часы заведений в городе или на указанной улице "
                "(«какие кафе есть на Пушкинской», «список супермаркетов»). "
                "НЕ ИСПОЛЬЗУЙ для подсчета количества («сколько аптек» -> count_places) и НЕ используй "
                "для запросов расстояния и поиска ближайшего к координатам («что ближе» -> nearest_place). "
                "Единицы: текстовая таблица до limit строк. "
                "Пример: find_places(kind='cafe', street='Пушкинская', limit=5)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": (
                            "Вид места латиницей: pharmacy, cafe, restaurant, fast_food, "
                            "supermarket, convenience, outpost, bank, school, kindergarten, fuel, park."
                        )
                    },
                    "street": {
                        "type": "string",
                        "description": "Название улицы на русском языке (например: 'Пушкинская', 'Большая Садовая'). Без номера дома."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Максимальное число строк в ответе (по умолчанию 5, максимум 20)."
                    }
                },
                "required": ["kind"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "top_brands",
            "description": (
                "Формирует рейтинг популярности сетей и брендов для заданного вида заведений. "
                "ИСПОЛЬЗУЙ для вопросов «какие сети популярны», «топ брендов», «у кого больше точек», «какие ПВЗ представлены». "
                "НЕ ИСПОЛЬЗУЙ для поиска конкретных адресов или отдельных заведений (для этого есть find_places). "
                "Единицы: возвращает таблицу 'бренд | количество точек'. "
                "Пример: top_brands(kind='outpost', limit=5)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Вид места латиницей: outpost (пункты выдачи), pharmacy (аптеки), supermarket (супермаркеты) и др."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Сколько лидирующих брендов включить в рейтинг (по умолчанию 5, от 1 до 15)."
                    }
                },
                "required": ["kind"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "geocode_address",
            "description": (
                "Определяет точные географические координаты (широта lat, долгота lon) дома или улицы в Ростове-на-Дону. "
                "ИСПОЛЬЗУЙ ВСЕГДА первым шагом, когда пользователь называет конкретный адрес («я на Белорусской 44», «рядом с Садовой 10») "
                "и нужно найти заведения поблизости. Полученные координаты затем передавай в nearest_place. "
                "НЕ ИСПОЛЬЗУЙ, если пользователь просто хочет перечень заведений на улице без координат (зови find_places). "
                "Единицы: градусы широты (lat) и долготы (lon) в стандарте WGS84. "
                "Пример: geocode_address(street='Белорусская', housenumber='44')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "street": {
                        "type": "string",
                        "description": "Название улицы на русском языке (например: 'Белорусская', 'Ворошиловский', 'Пушкинская')."
                    },
                    "housenumber": {
                        "type": "string",
                        "description": "Номер дома строкой (например: '44', '10/2', '5А'). Если дом неизвестен — опустите."
                    }
                },
                "required": ["street"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "nearest_place",
            "description": (
                "Находит ближайшие заведения заданного вида к географической точке (lat, lon) с расчетом точного расстояния. "
                "ИСПОЛЬЗУЙ, когда даны координаты (например: 47.2357, 39.7015) или когда координаты получены из geocode_address. "
                "НЕ ИСПОЛЬЗУЙ с выдуманными координатами: если дан адрес строкой, сначала вызови geocode_address! "
                "НЕ ИСПОЛЬЗУЙ для простого подсчета общего числа заведений в городе (зови count_places). "
                "Единицы: широта и долгота — градусы float; результат содержит расстояние distance_m в метрах. "
                "Пример: nearest_place(lat=47.2791, lon=39.7493, kind='pharmacy', limit=1)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {
                        "type": "number",
                        "description": "Географическая широта точки (float, около 47.22 для Ростова)."
                    },
                    "lon": {
                        "type": "number",
                        "description": "Географическая долгота точки (float, около 39.71 для Ростова)."
                    },
                    "kind": {
                        "type": "string",
                        "description": "Вид места латиницей: pharmacy, cafe, bank, school, outpost, supermarket, fuel."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Сколько ближайших заведений вернуть (по умолчанию 1, от 1 до 10)."
                    }
                },
                "required": ["lat", "lon", "kind"]
            }
        }
    }
]

# Схемы для веб-приложения

TOOL_GET_PLACE_DETAILS_SHORT = {
    "type": "function",
    "function": {
        "name": "get_place_details",
        "description": "Подробная карточка заведения по его числовому ID (адрес, телефон, часы работы, сайт).",
        "parameters": {
            "type": "object",
            "properties": {
                "place_id": {
                    "type": "integer",
                    "description": "ID заведения из базы."
                }
            },
            "required": ["place_id"]
        }
    }
}

TOOL_SEMANTIC_SEARCH_SHORT = {
    "type": "function",
    "function": {
        "name": "semantic_search",
        "description": "Семантический поиск заведений по блюдам, кухне или атмосфере (например, пельмени, круассаны).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Поисковый запрос (блюдо, ассоциация, атмосфера)."
                },
                "limit": {
                    "type": "integer",
                    "description": "Количество результатов."
                }
            },
            "required": ["query"]
        }
    }
}

TOOL_GET_PLACE_DETAILS_FULL = {
    "type": "function",
    "function": {
        "name": "get_place_details",
        "description": (
            "Возвращает полную карточку конкретного заведения по его уникальному ID: "
            "название, точный адрес, телефон, часы работы, сайт, признак круглосуточности и координаты. "
            "ИСПОЛЬЗУЙ, когда пользователь спрашивает детали заведения по его ID или названию "
            "(«подробнее про ID 1687», «телефон заведения 1451», «режим работы этого кафе»). "
            "НЕ ИСПОЛЬЗУЙ для общего поиска заведений по городу или кухне (зови find_places или semantic_search). "
            "Пример: get_place_details(place_id=1459)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "place_id": {
                    "type": "integer",
                    "description": "Уникальный целочисленный идентификатор заведения (ID) из базы OpenStreetMap."
                }
            },
            "required": ["place_id"]
        }
    }
}

TOOL_SEMANTIC_SEARCH_FULL = {
    "type": "function",
    "function": {
        "name": "semantic_search",
        "description": (
            "Выполняет умный семантический векторный поиск по векторной базе ChromaDB с кулинарными ассоциациями. "
            "ИСПОЛЬЗУЙ, когда пользователь описывает блюда, кухню, атмосферу или забыл точное название заведения "
            "(«где поесть пельмени», «сочные хинкали», «уютная кофейня с круассанами», «грузинская кухня»). "
            "НЕ ИСПОЛЬЗУЙ для поиска рядом с конкретным адресом пользователя (для этого сначала вызови geocode_address, "
            "а затем nearest_place с параметром query)! "
            "НЕ ИСПОЛЬЗУЙ для подсчета общего количества мест в городе (зови count_places). "
            "Пример: semantic_search(query='пельмени со сметаной', limit=5)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Поисковая фраза на русском языке: блюдо, кухня, атмосфера (например: 'пельмени', 'кофе и круассаны')."
                },
                "limit": {
                    "type": "integer",
                    "description": "Количество возвращаемых заведений (по умолчанию 5, от 1 до 10)."
                }
            },
            "required": ["query"]
        }
    }
}

WEB_TOOLS_SHORT = TOOLS_SHORT + [TOOL_SEMANTIC_SEARCH_SHORT, TOOL_GET_PLACE_DETAILS_SHORT]
WEB_TOOLS_FULL = TOOLS_FULL + [TOOL_SEMANTIC_SEARCH_FULL, TOOL_GET_PLACE_DETAILS_FULL]
