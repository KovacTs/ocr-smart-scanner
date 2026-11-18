import os
from pathlib import Path
from typing import Optional
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
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days access token
    REFRESH_TOKEN_EXPIRE_DAYS: int = 60  # 60 days refresh token
    SIGNED_URL_EXPIRE_SECONDS: int = 900  # 15 minutes for ephemeral signed file URLs
    ALLOWED_ORIGINS: str = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:8081,http://localhost:19006,http://localhost:3000",
    )
    MAX_UPLOAD_SIZE_BYTES: int = 20 * 1024 * 1024  # 20 MB

    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'ocr_scanner.db'}"
    )
    UPLOADS_DIR: Path = Path(os.getenv("UPLOADS_DIR", str(BASE_DIR / "uploads")))

    # Configuración de Almacenamiento (LOCAL vs S3 compatible con AWS y Oracle Cloud OCI)
    STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "LOCAL")  # LOCAL | S3
    S3_ENDPOINT_URL: Optional[str] = os.getenv("S3_ENDPOINT_URL", None)  # OCI: https://{namespace}.compat.objectstorage.{region}.oraclecloud.com
    S3_REGION_NAME: str = os.getenv("S3_REGION_NAME", "us-east-1")
    S3_BUCKET_NAME: Optional[str] = os.getenv("S3_BUCKET_NAME", None)
    S3_ACCESS_KEY_ID: Optional[str] = os.getenv("S3_ACCESS_KEY_ID", None)
    S3_SECRET_ACCESS_KEY: Optional[str] = os.getenv("S3_SECRET_ACCESS_KEY", None)
    S3_USE_SSL: bool = True
    S3_PRESIGN_EXPIRATION_SECONDS: int = 3600

    # Parámetros del Pipeline de Enfoque y WebP
    BLUR_THRESHOLD_DEFAULT: float = 240.0
    WEBP_MAX_DIMENSION: int = 2400
    WEBP_QUALITY: int = 82
    THUMBNAIL_WIDTH: int = 400
    THUMBNAIL_QUALITY: int = 70

    # Parámetros del Extractor Semántico LLM / VLM (Hito 2: Ollama Local / Gemini Cloud)
    LLM_PROVIDER: str = "AUTO"  # AUTO | OLLAMA | GEMINI | DISABLED
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:3b"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-flash-lite-latest"
    LLM_TIMEOUT_SECONDS: float = 6.0

    # Configuración de Cola Asíncrona (Redis + Arq)
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    USE_ASYNC_QUEUE: bool = os.getenv("USE_ASYNC_QUEUE", "false").lower() in (
        "true",
        "1",
        "yes",
    )

    # Autenticación con Google (OAuth 2.0 / OpenID Connect)
    GOOGLE_CLIENT_ID: Optional[str] = os.getenv("GOOGLE_CLIENT_ID", None)

    model_config = {
        "env_file": str(BASE_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

if settings.DATABASE_URL.startswith("postgresql://"):
    settings.DATABASE_URL = settings.DATABASE_URL.replace(
        "postgresql://", "postgresql+psycopg2://", 1
    )
elif settings.DATABASE_URL.startswith("sqlite:///"):
    db_raw_path = settings.DATABASE_URL.replace("sqlite:///", "")
    Path(db_raw_path).parent.mkdir(parents=True, exist_ok=True)

