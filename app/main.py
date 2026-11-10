from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

from app.agent.engine import setup_agents_sdk
from app.config import HOST, PORT
from app.db.database import init_db
from app.routers.auth import router as auth_router
from app.routers.chat import router as chat_router
from app.routers.places import router as places_router
from app.vector.indexer import ensure_index
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rostov_guide")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Инициализация проекта...")

    await init_db()
    
    ensure_index()
    
    setup_agents_sdk()
    logger.info("Проект запущен")
    yield
    logger.info("Выключение сервиса...")


app = FastAPI(
    title="Ростовская справочная система",
    description="MVP: асинхронность, многопользовательский режим, авторизация, SSE",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(places_router)

# Serve Static UI
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "API запущено. UI тут: app/static."}


@app.get("/health")
async def health_check():
    return {"status": "ok", "app": "rostov-agentic-city-guide"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)
