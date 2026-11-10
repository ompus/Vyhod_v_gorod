from __future__ import annotations

import sqlite3
from typing import Any
from fastapi import APIRouter, HTTPException, Query

from app.config import POI_DB_PATH
from app.vector.indexer import search_semantic

router = APIRouter(prefix="/api/places", tags=["places"])


@router.get("/semantic")
async def semantic_places(
    q: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=20),
    kind: str | None = Query(None),
):
    """Direct semantic vector search endpoint."""
    return search_semantic(q, limit=limit, kind_filter=kind)


@router.get("/{place_id}")
async def get_place(place_id: int):
    """Fetch details of a place by ID."""
    with sqlite3.connect(POI_DB_PATH) as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            "SELECT id, osm_type, category, kind, name, brand, street, housenumber, "
            "opening_hours, is_24_7, cuisine, phone, website, lat, lon FROM poi WHERE id = ?",
            (place_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Место не найдено")
        return dict(row)
