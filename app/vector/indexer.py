from __future__ import annotations

import logging
import sqlite3
from typing import Any
import chromadb
from chromadb.config import Settings

from app.config import CHROMA_DIR, POI_DB_PATH

logger = logging.getLogger(__name__)

COLLECTION_NAME = "rostov_places_semantic"

CUISINE_EXPANSIONS: dict[str, str] = {
    "russian": "русская кухня, домашняя еда, пельмени, вареники, блины, борщ, сырники, солянка, котлеты, квас",
    "georgian": "грузинская кухня, хинкали, хачапури, шашлык, харчо, сулугуни, сациви",
    "caucasian": "кавказская кухня, хинкали, люля-кебаб, шашлык, долма, мангал, лаваш",
    "italian": "итальянская кухня, пицца, паста, лазанья, ризотто, тирамису",
    "pizza": "пиццерия, пицца, паста, итальянские блюда, моцарелла",
    "burger": "бургеры, бургерная, картофель фри, чизбургер, наггетсы, фастфуд",
    "sushi": "суши, роллы, японская кухня, сашими, васаби",
    "japanese": "японская кухня, суши, роллы, рамен, том ям, мисо",
    "asian": "азиатская кухня, вок, лапша wok, рис, рамен, пад тай",
    "coffee_shop": "кофейня, кофе, капучино, латте, десерты, выпечка, круассаны, чизкейк, завтраки",
    "tea": "чайная, чайная церемония, чай, десерты",
    "bakery": "пекарня, пирожки, булочки, свежий хлеб, багет, выпечка, круассаны",
    "kebab": "шаурма, донер, кебаб, шашлык, мясо на гриле",
    "regional": "ростовская кухня, донская кухня, уха, раки, домашняя еда",
}

KIND_LABELS: dict[str, str] = {
    "restaurant": "Ресторан",
    "cafe": "Кафе",
    "fast_food": "Фастфуд / Быстрое питание",
    "bar": "Бар",
    "pub": "Паб",
    "bakery": "Пекарня / Кондитерская",
    "supermarket": "Супермаркет",
    "pharmacy": "Аптека",
    "outpost": "Пункт выдачи заказов",
    "park": "Парк / Сквер",
}

_chroma_client = None
_collection = None


def get_chroma_collection():
    global _chroma_client, _collection
    if _collection is None:
        _chroma_client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        _collection = _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def build_semantic_text(row: dict[str, Any]) -> str:
    name = row.get("name") or "Заведение без названия"
    kind = row.get("kind") or ""
    kind_label = KIND_LABELS.get(kind, kind)
    cuisine = (row.get("cuisine") or "").lower()
    street = row.get("street") or ""
    house = row.get("housenumber") or ""
    brand = row.get("brand") or ""
    hours = row.get("opening_hours") or ""

    # Extra culinary associations
    cuisine_keywords = []
    for c_key, c_expansion in CUISINE_EXPANSIONS.items():
        if c_key in cuisine:
            cuisine_keywords.append(c_expansion)

    # Name-based heuristics (e.g. if name contains пельмен / вареник / хинкали)
    lower_name = name.lower()
    if any(w in lower_name for w in ["пельмен", "вареник", "лепк", "трактир", "русск", "мама"]):
        cuisine_keywords.append("русская традиционная еда, пельмени со сметаной, домашние вареники, горячие обеды")
    if any(w in lower_name for w in ["хинкал", "хачапур", "грузин", "сули", "генацвале"]):
        cuisine_keywords.append("грузинская кухня, хинкали с мясом, сочные хачапури")
    if any(w in lower_name for w in ["кофе", "coffee", "cup", "зерно", "ростер"]):
        cuisine_keywords.append("кофейня, свежесваренный кофе, десерты и завтраки")
    if any(w in lower_name for w in ["бургер", "burger"]):
        cuisine_keywords.append("бургеры, сытные котлеты, картошка, фастфуд")

    expansions_text = " ".join(cuisine_keywords)

    doc = (
        f"Название: {name}. "
        f"Тип: {kind_label} ({kind}). "
        f"Кухня и блюда: {cuisine or 'смешанная'}. {expansions_text}. "
        f"Бренд: {brand}. "
        f"Адрес: г. Ростов-на-Дону, {street} {house}. "
        f"Часы работы: {hours or 'не указаны'}."
    )
    return doc


def ensure_index() -> int:
    """Build semantic vector index from poi.sqlite if not already indexed."""
    col = get_chroma_collection()
    count = col.count()
    if count > 0:
        logger.info(f"Семантический вектор построен: {count} объектов.")
        return count

    if not POI_DB_PATH.exists():
        logger.warning(f"База данных не найдена по пути {POI_DB_PATH}")
        return 0

    logger.info("Инициализация семантического индекса из poi.sqlite...")
    with sqlite3.connect(POI_DB_PATH) as con:
        con.row_factory = sqlite3.Row
        # Priority on food places, followed by named POIs
        cur = con.cursor()
        cur.execute("""
            SELECT id, osm_type, category, kind, name, brand, street, housenumber,
                   opening_hours, is_24_7, cuisine, lat, lon
            FROM poi
            WHERE kind IN ('restaurant', 'cafe', 'fast_food', 'bar', 'pub', 'bakery')
               OR (name IS NOT NULL AND kind IN ('supermarket', 'pharmacy', 'park', 'cinema', 'bookstore'))
            ORDER BY (kind IN ('restaurant', 'cafe', 'fast_food')) DESC, id ASC
            LIMIT 1500
        """)
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        return 0

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for r in rows:
        place_id = str(r["id"])
        doc = build_semantic_text(r)
        meta = {
            "id": r["id"],
            "name": r.get("name") or "Без названия",
            "kind": r.get("kind") or "place",
            "cuisine": r.get("cuisine") or "",
            "street": r.get("street") or "",
            "housenumber": r.get("housenumber") or "",
            "opening_hours": r.get("opening_hours") or "",
            "is_24_7": bool(r.get("is_24_7")),
            "lat": float(r["lat"]) if r.get("lat") else 0.0,
            "lon": float(r["lon"]) if r.get("lon") else 0.0,
            "brand": r.get("brand") or "",
        }
        ids.append(place_id)
        documents.append(doc)
        metadatas.append(meta)

    # Batch add in chunks of 200
    batch_size = 200
    for i in range(0, len(ids), batch_size):
        end = i + batch_size
        col.add(
            ids=ids[i:end],
            documents=documents[i:end],
            metadatas=metadatas[i:end],
        )

    indexed_count = col.count()
    logger.info(f"Векторный индекс создан: {indexed_count} мест.")
    return indexed_count


def search_semantic(
    query: str,
    limit: int = 5,
    kind_filter: str | None = None,
) -> list[dict[str, Any]]:
    """
    Search places by semantic similarity.
    Example: 'пельмени', 'где вкусно позавтракать', 'уютная кофейня с десертами'.
    """
    col = get_chroma_collection()
    if col.count() == 0:
        ensure_index()

    where_filter = None
    if kind_filter:
        where_filter = {"kind": kind_filter}

    FOOD_KEYWORDS = [
        "поесть", "еда", "блюд", "заведен", "ресторан", "кафе", "пельмен",
        "хинкал", "кофе", "выпечк", "бургер", "пицц", "шашлык", "обед",
        "ужин", "завтрак", "вкусно", "столов", "ланч", "суши", "ролл", "кухн"
    ]
    is_food_query = any(k in query.lower() for k in FOOD_KEYWORDS)

    try:
        # Fetch more candidates to filter cleanly
        fetch_count = min(max(limit * 3, 15), 40)
        results = col.query(
            query_texts=[query],
            n_results=fetch_count,
            where=where_filter,
            include=["metadatas", "documents", "distances"],
        )
    except Exception as e:
        logger.error(f"Error executing vector query: {e}")
        return []

    if not results or not results["ids"] or not results["ids"][0]:
        return []

    metas = results["metadatas"][0]
    docs = results["documents"][0]
    distances = results["distances"][0] if "distances" in results and results["distances"] else [0.5] * len(metas)

    food_kinds = {"restaurant", "cafe", "fast_food", "bakery", "bar", "pub"}

    items = []
    for meta, doc, dist in zip(metas, docs, distances):
        kind = meta.get("kind", "")
        if is_food_query and kind not in food_kinds:
            # Skip non-food places (e.g. parks, supermarkets, pharmacies) on dining queries
            continue

        # Convert cosine distance to match percentage
        match_score = round(max(0.0, (1.0 - dist)) * 100)
        item = dict(meta)
        item["match_score"] = match_score
        item["match_reason"] = f"Семантическое соответствие: {match_score}%"
        items.append(item)
        if len(items) >= limit:
            break

    return items
