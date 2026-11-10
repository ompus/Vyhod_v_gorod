from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from app.auth.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.db.database import create_user, get_user_by_username
from app.db.models import TokenResponse, UserCreate, UserLogin, UserResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
async def register(payload: UserCreate):
    existing = await get_user_by_username(payload.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Пользователь с таким именем уже существует",
        )

    pwd_hash = hash_password(payload.password)
    user_dict = await create_user(payload.username, pwd_hash)
    token = create_access_token({"sub": str(user_dict["id"])})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user_dict,
    }


@router.post("/login", response_model=TokenResponse)
async def login(payload: UserLogin):
    user_dict = await get_user_by_username(payload.username)
    if not user_dict or not verify_password(payload.password, user_dict["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверное имя пользователя или пароль",
        )

    token = create_access_token({"sub": str(user_dict["id"])})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user_dict["id"],
            "username": user_dict["username"],
            "created_at": user_dict["created_at"],
        },
    }


@router.get("/me", response_model=UserResponse)
async def me(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "username": current_user["username"],
        "created_at": current_user["created_at"],
    }
