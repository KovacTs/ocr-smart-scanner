from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import auth, scans
from app.core.config import settings
from app.db.database import Base, engine
import app.models.user  # noqa: F401
import app.models.scan  # noqa: F401

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "Sistema de Escaneo Documental Inteligente con Auto-Captura por Enfoque "
        "(Varianza del Laplaciano), Compresión WebP Multi-Nivel, Normalización de "
        "Facturas/Boletas y Exportación Contable a Excel (.xlsx)."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix=settings.API_V1_STR)
app.include_router(scans.router, prefix=settings.API_V1_STR)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
    }
