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


settings = Settings()
settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
