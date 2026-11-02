import io
from pathlib import Path
from typing import Any, Optional, Tuple
import cv2
import numpy as np
from PIL import Image, ImageOps
from app.core.config import settings


def detect_document_roi(gray_img: np.ndarray) -> Tuple[np.ndarray, bool, list[int]]:
    """
    Detecta la Región de Interés (ROI) del documento o sección de boleta (ej. detalle de venta).
    Soporta tanto documentos completos (4 bordes visibles) como acercamientos parciales a boletas
    donde los bordes del papel quedan fuera del encuadre.
    """
    h, w = gray_img.shape[:2]
    blurred = cv2.GaussianBlur(gray_img, (5, 5), 0)
    edged = cv2.Canny(blurred, 35, 120)

    contours, _ = cv2.findContours(
        edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]

    min_doc_area = (w * h) * 0.15

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_doc_area:
            continue
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        x, y, cw, ch = cv2.boundingRect(approx)
        if cw > w * 0.30 and ch > h * 0.30:
            roi = gray_img[y : y + ch, x : x + cw]
            return roi, True, [int(x), int(y), int(cw), int(ch)]

    # Acercamiento parcial (ej: detalle de venta de una boleta donde no entran los 4 bordes del papel)
    pad_x, pad_y = int(w * 0.10), int(h * 0.10)
    roi = gray_img[pad_y : h - pad_y, pad_x : w - pad_x]
    edge_roi = edged[pad_y : h - pad_y, pad_x : w - pad_x]
    edge_density = float(np.count_nonzero(edge_roi)) / float(roi.size or 1)
    # También validar desviación estándar local (contraste de texto sobre papel)
    contrast_std = float(np.std(roi))
    doc_detected = (edge_density > 0.0025) or (contrast_std > 14.0)
    return roi, doc_detected, [pad_x, pad_y, w - 2 * pad_x, h - 2 * pad_y]


def compute_text_patch_sharpness(roi: np.ndarray) -> float:
    """
    Calcula la nitidez (Varianza del Laplaciano) dividiendo el ROI en una cuadrícula de 8x8 bloques
    y tomando el percentil 85 de los bloques con mayor detalle (donde se ubican las letras y números).
    Esto evita que los espacios en blanco del papel térmico diluyan el puntaje de enfoque.
    """
    rh, rw = roi.shape[:2]
    if rh < 32 or rw < 32:
        return float(cv2.Laplacian(roi, cv2.CV_64F).var())

    grid_rows, grid_cols = 8, 8
    step_y = rh // grid_rows
    step_x = rw // grid_cols
    patch_scores: list[float] = []

    for r in range(grid_rows):
        for c in range(grid_cols):
            patch = roi[
                r * step_y : (r + 1) * step_y,
                c * step_x : (c + 1) * step_x,
            ]
            if patch.size > 0:
                score = float(cv2.Laplacian(patch, cv2.CV_64F).var())
                patch_scores.append(score)

    if not patch_scores:
        return float(cv2.Laplacian(roi, cv2.CV_64F).var())

    # Percentil 85 representa la nitidez real sobre las líneas de texto del documento/boleta
    p85_score = float(np.percentile(patch_scores, 85))
    global_score = float(cv2.Laplacian(roi, cv2.CV_64F).var())
    return max(p85_score * 0.65, global_score)


def analyze_focus_and_roi(
    image_bytes: bytes, threshold: Optional[float] = None
) -> dict[str, Any]:
    """
    Evalúa si el documento o sección de boleta está correctamente enfocado.
    """
    target_threshold = (
        threshold if threshold is not None else settings.BLUR_THRESHOLD_DEFAULT
    )
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if img is None:
        return {
            "is_focused": False,
            "document_detected": False,
            "blur_score": 0.0,
            "threshold": target_threshold,
            "roi_bbox": None,
            "status_color": "RED",
            "message": "Imagen inválida o corrupta",
        }

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    eval_h, eval_w = gray.shape[:2]
    if max(eval_h, eval_w) > 1000:
        scale = 1000.0 / max(eval_h, eval_w)
        gray = cv2.resize(
            gray, (int(eval_w * scale), int(eval_h * scale)), interpolation=cv2.INTER_AREA
        )

    roi, doc_detected, bbox = detect_document_roi(gray)
    sharpness = compute_text_patch_sharpness(roi)
    blur_score = round(sharpness, 2)

    if not doc_detected:
        return {
            "is_focused": False,
            "document_detected": False,
            "blur_score": blur_score,
            "threshold": target_threshold,
            "roi_bbox": bbox,
            "status_color": "RED",
            "message": "Apunte al texto de la boleta o factura",
        }

    is_focused = blur_score >= target_threshold
    if is_focused:
        status_color = "GREEN"
        message = f"Enfoque óptimo ({blur_score:.0f}) - Capturando..."
    else:
        status_color = "YELLOW"
        message = f"Ajustando foco ({blur_score:.0f}/{target_threshold:.0f}) - Mantenga firme"

    return {
        "is_focused": is_focused,
        "document_detected": True,
        "blur_score": blur_score,
        "threshold": target_threshold,
        "roi_bbox": bbox,
        "status_color": status_color,
        "message": message,
    }


def optimize_and_save_webp(
    image_bytes: bytes, user_id: str, scan_id: str
) -> dict[str, Any]:
    """
    Pipeline de optimización de almacenamiento:
    1. Si el cliente móvil ya comprimió a WebP con resolución <= 2400px, guarda los bytes directos
       evitando una doble compresión con pérdida que degradaría los bordes de las letras.
    2. Si es JPEG/PNG en crudo (> 2400px), reescala con filtro Lanczos y codifica a WebP 82%.
    3. Genera miniatura WebP de 400px con calidad 70% (~30KB).
    """
    original_size = len(image_bytes)
    img = Image.open(io.BytesIO(image_bytes))
    input_format = (img.format or "").upper()
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    w, h = img.size
    max_dim = settings.WEBP_MAX_DIMENSION

    scan_dir: Path = settings.UPLOADS_DIR / user_id / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)

    main_path = scan_dir / "document.webp"
    thumb_path = scan_dir / "thumbnail.webp"

    if input_format == "WEBP" and max(w, h) <= max_dim:
        # Guardar directamente el WebP generado en el cliente sin re-comprimir
        main_path.write_bytes(image_bytes)
        img_main = img
        # Estimar peso original JPEG (~8x el WebP comprimido) para telemetría
        estimated_raw_size = original_size * 8
    else:
        if max(w, h) > max_dim:
            scale = max_dim / float(max(w, h))
            new_size = (int(w * scale), int(h * scale))
            img_main = img.resize(new_size, Image.Resampling.LANCZOS)
        else:
            img_main = img.copy()

        img_main.save(
            main_path,
            format="WEBP",
            quality=settings.WEBP_QUALITY,
            method=6,
        )
        estimated_raw_size = original_size

    tw = settings.THUMBNAIL_WIDTH
    th = max(1, int(img_main.size[1] * (tw / float(img_main.size[0]))))
    img_thumb = img_main.resize((tw, th), Image.Resampling.LANCZOS)
    img_thumb.save(
        thumb_path,
        format="WEBP",
        quality=settings.THUMBNAIL_QUALITY,
        method=4,
    )

    optimized_size = main_path.stat().st_size
    thumb_size = thumb_path.stat().st_size

    return {
        "image_url": f"/api/scans/files/{user_id}/{scan_id}/document.webp",
        "thumbnail_url": f"/api/scans/files/{user_id}/{scan_id}/thumbnail.webp",
        "main_disk_path": str(main_path),
        "original_size_bytes": estimated_raw_size,
        "file_size_bytes": optimized_size,
        "thumbnail_size_bytes": thumb_size,
    }
