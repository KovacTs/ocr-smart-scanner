# Smart OCR Scanner: Auto-Captura por Enfoque, Compresión WebP, Normalización y Exportación a Excel

Sistema completo de escaneo documental inteligente diseñado con:
- **Backend en Python (FastAPI + OpenCV + Pillow WebP + Tesseract + OpenPyXL + SQLAlchemy + JWT)**
- **Cliente Móvil Multiplataforma (React Native + Expo + TypeScript)**

---

## Arquitectura e Innovaciones Implementadas

1. **Validación de Enfoque en Tiempo Real y Auto-Captura (`vision_service.py` & `SmartCameraScreen.tsx`):**
   - Filtro inercial mediante giroscopio (`expo-sensors`) que bloquea capturas si hay movimiento en la mano del usuario.
   - Detección del contorno del documento (Región de Interés / ROI) con OpenCV y cálculo de la **Varianza del Laplaciano** ($\operatorname{Var}(\nabla^2 I_{\text{ROI}})$).
   - **Ventana de persistencia:** Exige 4 cuadros consecutivos enfocados antes de disparar automáticamente en alta resolución.
2. **Pipeline de Optimización y Compresión WebP Multi-Nivel:**
   - Compresión en cliente (`expo-image-manipulator`) y servidor (`Pillow`): normaliza el lado mayor a máx. `2400px` (~300 DPI) y codifica a **WebP calidad 82%** (`document.webp`, reduciendo ~90% el peso frente a un JPEG de 5 MB) junto con una miniatura de `400px` calidad 70% (`thumbnail.webp`, ~30 KB).
3. **Motor de Normalización y Filtrado de Ruido en Facturas y Boletas (`ocr_normalizer.py`):**
   - Limpia texto basura de boletas térmicas y facturas (leyendas del SII, timbres electrónicos, números de tarjeta POS enmascarados, líneas divisorias).
   - Estandariza RUTs con validación Módulo 11 (`XX.XXX.XXX-X`), fechas a ISO 8601 (`YYYY-MM-DD`), y montos (`Neto`, `IVA`, `Exento`, `Total`).
4. **Historial con Control de Versiones y Exportación Contable a Excel (`excel_service.py`):**
   - Cada edición manual crea una nueva versión auditable en `SCAN_VERSIONS` manteniendo intacta la versión original del OCR y la imagen de respaldo.
   - Exportación individual y por lotes a `.xlsx` con formato de moneda, autofiltros, hipervínculos a la imagen WebP y fórmulas automáticas `=SUM(...)`.

---

## Ejecución Rápida

### 1. Servidor Backend (FastAPI)

```bash
cd /home/kovac/Proyectos/ocr-smart-scanner/backend
./.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
- Documentación interactiva Swagger UI: `http://localhost:8000/docs`

### 2. Pruebas Automatizadas End-to-End

```bash
cd /home/kovac/Proyectos/ocr-smart-scanner/backend
PYTHONPATH=. ./.venv/bin/pytest -v tests/test_api_pipeline.py
```

### 3. Aplicación Móvil (React Native / Expo)

```bash
cd /home/kovac/Proyectos/ocr-smart-scanner/mobile
npm install
npx expo start
```
