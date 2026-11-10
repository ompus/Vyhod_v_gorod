from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


# User Models
class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=4, max_length=100)


class UserLogin(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    created_at: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# Conversation Models
class ConversationCreate(BaseModel):
    title: str | None = "Новый диалог"


class ConversationResponse(BaseModel):
    id: str
    user_id: int
    title: str
    created_at: str
    updated_at: str


# Message Models
class ThoughtStep(BaseModel):
    step: int
    title: str
    detail: str | None = None
    tool: str | None = None
    args: dict[str, Any] | None = None
    result: Any | None = None
    timestamp: str | None = None


class PlaceCard(BaseModel):
    id: int
    name: str
    kind: str
    cuisine: str | None = None
    street: str | None = None
    housenumber: str | None = None
    opening_hours: str | None = None
    is_24_7: bool | None = None
    distance_m: int | None = None
    lat: float | None = None
    lon: float | None = None
    match_reason: str | None = None


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    thoughts: list[ThoughtStep] = []
    places: list[PlaceCard] = []
    created_at: str


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(..., min_length=1)
