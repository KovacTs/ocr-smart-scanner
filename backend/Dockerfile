FROM python:3.11-slim

# Evitar prompts interactivos y optimizar logs de Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

# Instalar dependencias del sistema necesarias para OpenCV, zxing-cpp y ONNX Runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar requirements e instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copiar el código del backend (incluyendo app/ocr_models/)
COPY . .

# Crear directorio de subidas
RUN mkdir -p uploads

# Exponer el puerto de FastAPI
EXPOSE 8000

# Healthcheck interno del contenedor
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Iniciar servidor Uvicorn optimizado para producción
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
