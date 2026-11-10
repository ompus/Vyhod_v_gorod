from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
import aiosqlite

from app.config import APP_DB_PATH
from app.db.models import ThoughtStep, PlaceCard


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_db_initialized = False


async def ensure_db_initialized() -> None:
    global _db_initialized
    if not _db_initialized:
        await init_db()
        _db_initialized = True


async def init_db() -> None:
    """Initialize application database schema."""
    global _db_initialized
    async with aiosqlite.connect(APP_DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                thoughts TEXT,
                places TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
        """)
        await db.commit()
    _db_initialized = True


# --- User CRUD ---

async def create_user(username: str, password_hash: str) -> dict[str, Any]:
    await ensure_db_initialized()
    async with aiosqlite.connect(APP_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        now = now_iso()
        cursor = await db.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, password_hash, now),
        )
        await db.commit()
        user_id = cursor.lastrowid
        return {"id": user_id, "username": username, "created_at": now}


async def get_user_by_username(username: str) -> dict[str, Any] | None:
    await ensure_db_initialized()
    async with aiosqlite.connect(APP_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, username, password_hash, created_at FROM users WHERE username = ?",
            (username,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    await ensure_db_initialized()
    async with aiosqlite.connect(APP_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, username, created_at FROM users WHERE id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


# --- Conversation CRUD ---

async def create_conversation(user_id: int, title: str = "Новый диалог") -> dict[str, Any]:
    await ensure_db_initialized()
    async with aiosqlite.connect(APP_DB_PATH) as db:
        conv_id = str(uuid.uuid4())
        now = now_iso()
        await db.execute(
            "INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conv_id, user_id, title, now, now),
        )
        await db.commit()
        return {
            "id": conv_id,
            "user_id": user_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
        }


async def get_user_conversations(user_id: int) -> list[dict[str, Any]]:
    await ensure_db_initialized()
    async with aiosqlite.connect(APP_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, user_id, title, created_at, updated_at FROM conversations "
            "WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_conversation(conv_id: str, user_id: int) -> dict[str, Any] | None:
    await ensure_db_initialized()
    async with aiosqlite.connect(APP_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, user_id, title, created_at, updated_at FROM conversations "
            "WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_conversation(conv_id: str, title: str | None = None) -> None:
    await ensure_db_initialized()
    async with aiosqlite.connect(APP_DB_PATH) as db:
        now = now_iso()
        if title:
            await db.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, conv_id),
            )
        else:
            await db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conv_id),
            )
        await db.commit()


async def delete_conversation(conv_id: str, user_id: int) -> bool:
    await ensure_db_initialized()
    async with aiosqlite.connect(APP_DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


# --- Message CRUD ---

async def add_message(
    conversation_id: str,
    role: str,
    content: str,
    thoughts: list[dict[str, Any]] | None = None,
    places: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    await ensure_db_initialized()
    async with aiosqlite.connect(APP_DB_PATH) as db:
        msg_id = str(uuid.uuid4())
        now = now_iso()
        thoughts_str = json.dumps(thoughts or [], ensure_ascii=False)
        places_str = json.dumps(places or [], ensure_ascii=False)
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, thoughts, places, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_id, conversation_id, role, content, thoughts_str, places_str, now),
        )
        # Update conversation updated_at
        await db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        await db.commit()
        return {
            "id": msg_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "thoughts": thoughts or [],
            "places": places or [],
            "created_at": now,
        }


async def get_conversation_messages(conversation_id: str) -> list[dict[str, Any]]:
    await ensure_db_initialized()
    async with aiosqlite.connect(APP_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, conversation_id, role, content, thoughts, places, created_at "
            "FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["thoughts"] = json.loads(d["thoughts"]) if d.get("thoughts") else []
                d["places"] = json.loads(d["places"]) if d.get("places") else []
                result.append(d)
            return result
