from __future__ import annotations

import json
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sse_starlette.sse import EventSourceResponse

from app.auth.security import get_current_user
from app.db.database import (
    add_message,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_conversation_messages,
    get_user_conversations,
    update_conversation,
)
from app.db.models import (
    ConversationCreate,
    ConversationResponse,
    MessageResponse,
)
from app.agent.engine import stream_agent_conversation

router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(current_user: dict = Depends(get_current_user)):
    return await get_user_conversations(current_user["id"])


@router.post("/conversations", response_model=ConversationResponse)
async def new_conversation(
    payload: ConversationCreate | None = None,
    current_user: dict = Depends(get_current_user),
):
    title = (payload.title if payload and payload.title else "Новый диалог").strip()
    return await create_conversation(current_user["id"], title=title)


@router.delete("/conversations/{conv_id}")
async def remove_conversation(
    conv_id: str,
    current_user: dict = Depends(get_current_user),
):
    ok = await delete_conversation(conv_id, current_user["id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    return {"status": "deleted"}


@router.get("/conversations/{conv_id}/messages", response_model=list[MessageResponse])
async def get_messages(
    conv_id: str,
    current_user: dict = Depends(get_current_user),
):
    conv = await get_conversation(conv_id, current_user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    return await get_conversation_messages(conv_id)


@router.get("/chat/stream")
async def chat_sse_stream(
    message: str = Query(..., min_length=1),
    conversation_id: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Server-Sent Events (SSE) streaming endpoint.
    Streams agent's thought process, tool invocations, place cards, and token stream.
    """
    user_id = current_user["id"]

    # 1. Resolve or create conversation
    if not conversation_id:
        title = message[:35] + "..." if len(message) > 35 else message
        conv = await create_conversation(user_id, title=title)
        conversation_id = conv["id"]
    else:
        conv = await get_conversation(conversation_id, user_id)
        if not conv:
            title = message[:35] + "..." if len(message) > 35 else message
            conv = await create_conversation(user_id, title=title)
            conversation_id = conv["id"]
        elif conv["title"] == "Новый диалог":
            new_title = message[:35] + "..." if len(message) > 35 else message
            await update_conversation(conversation_id, title=new_title)

    # 2. Get existing history for context
    history = await get_conversation_messages(conversation_id)

    # 3. Save user message to database
    await add_message(conversation_id, role="user", content=message)

    # 4. SSE Generator
    async def sse_event_generator():
        # First send session meta event so client knows conversation_id
        yield {
            "event": "session",
            "data": json.dumps({"conversation_id": conversation_id}, ensure_ascii=False),
        }

        final_content = ""
        final_thoughts: list[dict[str, Any]] = []
        final_places: list[dict[str, Any]] = []

        async for item in stream_agent_conversation(conversation_id, message, history):
            event_type = item.get("event")
            event_data = item.get("data")

            if event_type == "token":
                final_content += event_data.get("text", "")
            elif event_type == "done":
                final_content = event_data.get("content", final_content)
                final_thoughts = event_data.get("thoughts", [])
                final_places = event_data.get("places", [])

            yield {
                "event": event_type,
                "data": json.dumps(event_data, ensure_ascii=False),
            }

        # Save assistant response to database on completion
        if final_content or final_places:
            await add_message(
                conversation_id,
                role="assistant",
                content=final_content or "Готово.",
                thoughts=final_thoughts,
                places=final_places,
            )

    return EventSourceResponse(sse_event_generator())
