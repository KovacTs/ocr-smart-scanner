import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    PROJECT_NAME: str = "Smart OCR Scanner API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api"

    SECRET_KEY: str = os.getenv(
        "SECRET_KEY", "super-secret-ocr-jwt-key-change-in-production-2026"
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'ocr_scanner.db'}"
    )
    UPLOADS_DIR: Path = BASE_DIR / "uploads"

    # Parámetros del Pipeline de Enfoque y WebP
    BLUR_THRESHOLD_DEFAULT: float = 380.0
    WEBP_MAX_DIMENSION: int = 2400
    WEBP_QUALITY: int = 82
    THUMBNAIL_WIDTH: int = 400
    THUMBNAIL_QUALITY: int = 70

    # Parámetros del Extractor Semántico LLM / VLM (Hito 2: Ollama Local / Gemini Cloud)
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "AUTO")  # AUTO | OLLAMA | GEMINI | DISABLED
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "4.5"))


settings = Settings()
settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
