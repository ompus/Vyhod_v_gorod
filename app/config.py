from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root (with fallback to .env.example)
ROOT_DIR = Path(__file__).resolve().parent.parent
if (ROOT_DIR / ".env").exists():
    load_dotenv(ROOT_DIR / ".env")
elif (ROOT_DIR / ".env.example").exists():
    load_dotenv(ROOT_DIR / ".env.example")

# Paths
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True, parents=True)

POI_DB_PATH = ROOT_DIR / "data" / "poi.sqlite"
APP_DB_PATH = DATA_DIR / "app.db"
CHROMA_DIR = DATA_DIR / "chroma"
CHROMA_DIR.mkdir(exist_ok=True, parents=True)

# LLM & Agent Config
API_KEY = os.getenv("API_KEY", "")
SERVICE = os.getenv("SERVICE", "polzaai")
BASE_URL = os.getenv(
    "BASE_URL",
    "https://polza.ai/api/v1" if SERVICE != "openai" else None,
)
MODEL = os.getenv(
    "MODEL",
    os.getenv(
        "OPENAI_DEFAULT_MODEL",
        "deepseek/deepseek-v4-flash-0731@provider=deepinfra/fp8&allow_fallbacks=false",
    ),
)
if MODEL:
    os.environ["OPENAI_DEFAULT_MODEL"] = MODEL

# Authentication Config
JWT_SECRET_KEY = os.getenv(
    "JWT_SECRET_KEY",
    "rostov-agentic-city-secret-key-change-in-production-2026",
)
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))  # 7 days

# Server Config
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))