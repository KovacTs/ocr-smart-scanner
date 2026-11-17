import logging
import shutil
import time
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, List, Optional
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.api.deps import get_current_user, get_optional_current_user
from app.core.config import settings
from app.core.queue import enqueue_scan_job
from app.core.security import verify_signed_file_signature
from app.db.database import get_db
from app.models.scan import Scan, ScanVersion, utc_now
from app.models.user import User
from app.services.storage_service import storage_service
from app.schemas.scan import (
    BatchDeleteRequest,
    BatchExportRequest,
    BatchRestoreRequest,
    FocusCheckResponse,
    ScanResponse,
    ScanUpdateRequest,
)
from app.services.excel_service import generate_scans_excel
from app.services.ocr_normalizer import (
    extract_raw_text_from_image,
    normalize_document_text,
)
from app.services.sii_barcode_service import (
    decode_barcodes_from_image,
    merge_sii_ted_with_ocr,
    parse_sii_ted_xml,
)
from app.services.vision_service import analyze_focus_and_roi, optimize_and_save_webp

logger = logging.getLogger("ocr_pipeline")

router = APIRouter(prefix="/scans", tags=["Escaneos, Enfoque, Normalización y Excel"])


def _validate_image_magic_bytes(image_bytes: bytes) -> None:
    """
    Valida tamaño máximo y números mágicos de cabecera (JPEG, PNG o WebP)
    para evitar la subida de archivos maliciosos o corruptos.
    """
    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo de imagen está vacío.",
        )
    if len(image_bytes) > settings.MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="El archivo excede el tamaño máximo permitido (20 MB).",
        )
    is_jpeg = image_bytes.startswith(b"\xff\xd8\xff")
    is_png = image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    is_webp = (
        len(image_bytes) >= 12
        and image_bytes[:4] == b"RIFF"
        and image_bytes[8:12] == b"WEBP"
    )
    if not (is_jpeg or is_png or is_webp):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato de archivo no válido. Solo se permiten imágenes JPEG, PNG o WebP.",
        )


def _to_decimal_amount(raw_val: Any) -> Decimal:
    """
    Convierte un monto numérico a Decimal exacto con 2 decimales (Numeric(14, 2))
    para evitar errores de redondeo de punto flotante en contabilidad.
    """
    try:
        if raw_val is None or raw_val == "":
            return Decimal("0.00")
        return Decimal(str(raw_val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


def _purge_scan_permanently(db: Session, scan: Scan) -> None:
    """
    Elimina físicamente los archivos en almacenamiento S3/disco local y borra el registro de la BD.
    """
    storage_service.delete_prefix(f"{scan.user_id}/{scan.id}")
    scan_dir = settings.UPLOADS_DIR / scan.user_id / scan.id
    if scan_dir.exists():
        shutil.rmtree(scan_dir, ignore_errors=True)
    db.delete(scan)


@router.post("/evaluate-focus", response_model=FocusCheckResponse)
async def evaluate_camera_focus(
    file: UploadFile = File(...),
    threshold: Optional[float] = Form(default=None),
    current_user: User = Depends(get_current_user),
):
    """
    Evalúa en tiempo real un fotograma del stream de cámara:
    1. Detecta si hay un documento encuadrado (ROI).
    2. Calcula la Varianza del Laplaciano exclusivamente sobre el ROI.
    3. Devuelve el semáforo de estado (RED, YELLOW, GREEN) para disparo asistido / auto-captura.
    """
    t0 = time.perf_counter()
    image_bytes = await file.read()
    _validate_image_magic_bytes(image_bytes)
    result = analyze_focus_and_roi(image_bytes, threshold=threshold)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        f"📸 [Enfoque] Score={result['blur_score']:.0f}/{result['threshold']:.0f} | "
        f"Estado={result['status_color']} | Doc={result['document_detected']} ({elapsed_ms}ms)"
    )
    return FocusCheckResponse(**result)


@router.post("", response_model=ScanResponse, status_code=status.HTTP_201_CREATED)
async def create_scan(
    file: UploadFile = File(...),
    raw_text_hint: Optional[str] = Form(default=None),
    sii_ted_xml: Optional[str] = Form(default=None),
    force_capture: bool = Form(default=False),
    blur_threshold: Optional[float] = Form(default=None),
    precomputed_blur_score: Optional[float] = Form(default=None),
    async_mode: bool = Form(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Pipeline completo de captura de documento:
    1. Valida el nivel de enfoque de la imagen (rechaza fotos borrosas salvo force_capture=True o Timbre SII leído).
       Si el cliente ya validó el foco en /evaluate-focus y envía precomputed_blur_score con force_capture=True,
       reutiliza dicho puntaje evitando recalcular el filtro Laplaciano sobre la imagen completa.
    2. Normaliza resolución (máx 2400px) y comprime a WebP (82% calidad) + Miniatura WebP (400px).
    3. Si async_mode=True: guarda el registro con status="PROCESSING", encola la tarea en Redis+Arq y responde en <200ms.
    4. Si async_mode=False (síncrono): ejecuta OCR visual, Timbre SII y LLM en el ciclo de petición.
    """
    t_pipeline_start = time.perf_counter()
    scan_id = str(uuid.uuid4())
    short_id = scan_id[:8]

    image_bytes = await file.read()
    _validate_image_magic_bytes(image_bytes)
    ted_from_client = parse_sii_ted_xml(sii_ted_xml) if sii_ted_xml else None

    if precomputed_blur_score is not None and (force_capture or ted_from_client is not None):
        target_thresh = (
            blur_threshold
            if blur_threshold is not None
            else settings.BLUR_THRESHOLD_DEFAULT
        )
        focus_info = {
            "is_focused": precomputed_blur_score >= target_thresh,
            "document_detected": True,
            "blur_score": round(float(precomputed_blur_score), 2),
            "threshold": target_thresh,
            "roi_bbox": None,
            "status_color": "GREEN",
            "message": "Enfoque pre-validado en captura",
        }
    else:
        focus_info = analyze_focus_and_roi(image_bytes, threshold=blur_threshold)

    if not force_capture and not focus_info["is_focused"] and ted_from_client is None:
        logger.warning(
            f"⚠️ [Scan #{short_id}] Imagen rechazada por desenfoque "
            f"(Score={focus_info['blur_score']:.0f} < Umbral={focus_info['threshold']:.0f})"
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "IMAGE_OUT_OF_FOCUS",
                "message": (
                    f"La imagen está desenfocada (Score: {focus_info['blur_score']} < "
                    f"Umbral: {focus_info['threshold']}). Mantenga el dispositivo estable."
                ),
                "focus_analysis": focus_info,
            },
        )

    t_webp = time.perf_counter()
    storage_info = optimize_and_save_webp(
        image_bytes=image_bytes,
        user_id=current_user.id,
        scan_id=scan_id,
    )
    webp_ms = int((time.perf_counter() - t_webp) * 1000)
    orig_kb = round(storage_info["original_size_bytes"] / 1024, 1)
    webp_kb = round(storage_info["file_size_bytes"] / 1024, 1)
    logger.info(
        f"🚀 [Scan #{short_id} - 1/4] Imagen recibida ({orig_kb}KB -> WebP {webp_kb}KB en {webp_ms}ms) | "
        f"BlurScore={focus_info['blur_score']}"
    )

    # Modo Asíncrono: Encolar a Redis/Arq cuando el cliente solicita async_mode=True
    if async_mode:
        scan = Scan(
            id=scan_id,
            user_id=current_user.id,
            status="PROCESSING",
            document_type="PENDIENTE",
            folio="PROCESANDO...",
            image_url=storage_info["image_url"],
            thumbnail_url=storage_info["thumbnail_url"],
            original_size_bytes=storage_info["original_size_bytes"],
            file_size_bytes=storage_info["file_size_bytes"],
            blur_score=focus_info["blur_score"],
            current_raw_text="Procesando extracción OCR en segundo plano...",
            current_normalized_data={"status": "PROCESSING"},
            issue_date=None,
            total_amount=Decimal("0.00"),
        )
        db.add(scan)
        db.commit()
        db.refresh(scan)

        logger.info(
            f"⏳ [Scan #{short_id}] Encolando trabajo en Redis/Arq para procesamiento asíncrono..."
        )
        await enqueue_scan_job(
            scan_id=scan_id,
            image_disk_path=storage_info["main_disk_path"],
            raw_text_hint=raw_text_hint,
            sii_ted_xml=sii_ted_xml,
        )
        return ScanResponse.model_validate(scan)

    t_ted = time.perf_counter()
    ted_data = ted_from_client or decode_barcodes_from_image(
        storage_info["main_disk_path"]
    )
    ted_ms = int((time.perf_counter() - t_ted) * 1000)
    if ted_data:
        logger.info(
            f"📊 [Scan #{short_id} - 2/4] Código SII detectado ({ted_data.get('source')}) en {ted_ms}ms -> "
            f"Folio={ted_data.get('document_number')}, Total=${ted_data.get('amounts', {}).get('total_amount')}"
        )
    else:
        logger.info(
            f"📊 [Scan #{short_id} - 2/4] Sin código de barras SII legible ({ted_ms}ms). Continuando con OCR visual..."
        )

    t_ocr = time.perf_counter()
    raw_text = extract_raw_text_from_image(
        image_path=storage_info["main_disk_path"],
        raw_text_hint=raw_text_hint,
    )
    ocr_ms = int((time.perf_counter() - t_ocr) * 1000)
    line_count = len([ln for ln in raw_text.splitlines() if ln.strip()])
    logger.info(
        f"🔍 [Scan #{short_id} - 3/4] OCR Visual (CLAHE + PP-OCRv4) completado en {ocr_ms}ms ({line_count} líneas extraídas)"
    )

    has_full_ted = bool(ted_data and ted_data.get("source") == "SII_TED_PDF417")
    t_norm = time.perf_counter()
    ocr_normalized = normalize_document_text(
        raw_text,
        image_path=None if raw_text_hint else storage_info["main_disk_path"],
        use_llm_fallback=not has_full_ted,
    )
    norm_ms = int((time.perf_counter() - t_norm) * 1000)
    normalized_data = merge_sii_ted_with_ocr(ted_data, ocr_normalized)

    if has_full_ted:
        ted_header = (
            f"[TIMBRE SII PDF417 VERIFICADO] "
            f"RUT: {normalized_data['issuer']['tax_id']} | "
            f"Comercio: {normalized_data['issuer']['legal_name']} | "
            f"Folio: {normalized_data['document_number']} | "
            f"Total: ${int(normalized_data['amounts']['total_amount'])}"
        )
        raw_text = f"{ted_header}\n{raw_text}".strip()

    amounts = normalized_data.get("amounts", {})
    total_amount_dec = _to_decimal_amount(amounts.get("total_amount", 0.0))

    scan = Scan(
        id=scan_id,
        user_id=current_user.id,
        document_type=normalized_data.get("document_type", "FACTURA_ELECTRONICA"),
        folio=normalized_data.get("document_number", "S/N"),
        image_url=storage_info["image_url"],
        thumbnail_url=storage_info["thumbnail_url"],
        original_size_bytes=storage_info["original_size_bytes"],
        file_size_bytes=storage_info["file_size_bytes"],
        blur_score=focus_info["blur_score"],
        current_raw_text=raw_text,
        current_normalized_data=normalized_data,
        issue_date=normalized_data.get("issue_date"),
        total_amount=total_amount_dec,
    )
    db.add(scan)

    if has_full_ted:
        mod_by = "SII_TED_PDF417"
    elif normalized_data.get("llm_refined"):
        mod_by = "OCR_LLM_HYBRID"
    else:
        mod_by = "OCR_AUTOMATIC"

    version_1 = ScanVersion(
        scan_id=scan_id,
        version_number=1,
        raw_text=raw_text,
        normalized_data=normalized_data,
        modified_by=mod_by,
    )
    db.add(version_1)
    db.commit()
    db.refresh(scan)

    total_ms = int((time.perf_counter() - t_pipeline_start) * 1000)
    issuer_info = normalized_data.get("issuer", {})
    logger.info(
        f"✅ [Scan #{short_id} - 4/4] COMPLETADO en {total_ms}ms (Norm/LLM: {norm_ms}ms, Método: {mod_by}) | "
        f"Tipo={scan.document_type} | Folio={scan.folio} | "
        f"RUT={issuer_info.get('tax_id') or 'S/RUT'} ({issuer_info.get('legal_name')}) | "
        f"Total=${scan.total_amount}"
    )

    return ScanResponse.model_validate(scan)


@router.get("", response_model=List[ScanResponse])
def list_user_scans(
    search: Optional[str] = Query(default=None),
    document_type: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    only_deleted: bool = Query(default=False),
    include_deleted: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Scan).filter(Scan.user_id == current_user.id)

    if only_deleted:
        query = query.filter(Scan.deleted_at.is_not(None))
    elif not include_deleted:
        query = query.filter(Scan.deleted_at.is_(None))

    if document_type:
        query = query.filter(Scan.document_type == document_type)
    if date_from:
        query = query.filter(Scan.issue_date >= date_from)
    if date_to:
        query = query.filter(Scan.issue_date <= date_to)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Scan.folio.ilike(term),
                Scan.current_raw_text.ilike(term),
                Scan.document_type.ilike(term),
            )
        )

    order_col = Scan.deleted_at.desc() if only_deleted else Scan.created_at.desc()
    scans = query.order_by(order_col).all()
    return [ScanResponse.model_validate(s) for s in scans]


@router.get("/trash", response_model=List[ScanResponse])
def list_trash_scans(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Lista los comprobantes que se encuentran en la Papelera de Reciclaje (Soft-Delete)
    conservando la evidencia tributaria e imagen WebP para recuperación.
    """
    scans = (
        db.query(Scan)
        .filter(Scan.user_id == current_user.id, Scan.deleted_at.is_not(None))
        .order_by(Scan.deleted_at.desc())
        .all()
    )
    return [ScanResponse.model_validate(s) for s in scans]


@router.post("/export-batch")
def export_scans_batch_excel(
    req: BatchExportRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Exporta un lote de escaneos activos filtrados o seleccionados a una planilla Excel (.xlsx)
    con formato contable, hipervínculos pre-firmados HMAC y fórmulas automáticas.
    """
    query = db.query(Scan).filter(
        Scan.user_id == current_user.id,
        Scan.deleted_at.is_(None),
    )

    if req.scan_ids:
        query = query.filter(Scan.id.in_(req.scan_ids))
    if req.document_type:
        query = query.filter(Scan.document_type == req.document_type)
    if req.date_from:
        query = query.filter(Scan.issue_date >= req.date_from)
    if req.date_to:
        query = query.filter(Scan.issue_date <= req.date_to)

    scans = query.order_by(Scan.issue_date.desc(), Scan.created_at.desc()).all()
    base_url = str(request.base_url).rstrip("/")
    excel_bytes = generate_scans_excel(scans, base_url=base_url)

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="reporte_escaneos_ocr.xlsx"'
        },
    )


@router.post("/delete-batch")
def delete_scans_batch(
    req: BatchDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Elimina uno o más escaneos seleccionados por el usuario.
    Por defecto aplica Soft-Delete (Papelera Tributaria: preserva registro e imagen WebP).
    Si req.permanent=True, purga definitivamente los archivos y registros.
    """
    if not req.scan_ids:
        return {"deleted_count": 0, "soft_deleted": not req.permanent}

    scans = (
        db.query(Scan)
        .filter(Scan.user_id == current_user.id, Scan.id.in_(req.scan_ids))
        .all()
    )
    deleted_count = 0
    now = utc_now()
    for scan in scans:
        if req.permanent:
            _purge_scan_permanently(db, scan)
        else:
            scan.deleted_at = now
        deleted_count += 1

    db.commit()
    return {"deleted_count": deleted_count, "soft_deleted": not req.permanent}


@router.post("/restore-batch")
def restore_scans_batch(
    req: BatchRestoreRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Restaura uno o más escaneos desde la Papelera de Reciclaje al historial activo.
    """
    if not req.scan_ids:
        return {"restored_count": 0}

    scans = (
        db.query(Scan)
        .filter(
            Scan.user_id == current_user.id,
            Scan.id.in_(req.scan_ids),
            Scan.deleted_at.is_not(None),
        )
        .all()
    )
    restored_count = 0
    for scan in scans:
        scan.deleted_at = None
        restored_count += 1

    db.commit()
    return {"restored_count": restored_count}


@router.get("/{scan_id}", response_model=ScanResponse)
def get_scan_detail(
    scan_id: str,
    include_deleted: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Scan).filter(
        Scan.id == scan_id, Scan.user_id == current_user.id
    )
    if not include_deleted:
        query = query.filter(Scan.deleted_at.is_(None))
    scan = query.first()
    if not scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Escaneo no encontrado.",
        )
    return ScanResponse.model_validate(scan)


@router.get("/{scan_id}/status")
def get_scan_status(
    scan_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Endpoint liviano para consultar el estado del procesamiento asíncrono
    ('PROCESSING', 'COMPLETED', 'FAILED') sin cargar todo el payload de versiones.
    """
    scan = (
        db.query(Scan)
        .filter(Scan.id == scan_id, Scan.user_id == current_user.id)
        .first()
    )
    if not scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Escaneo no encontrado.",
        )
    return {
        "id": scan.id,
        "status": scan.status,
        "folio": scan.folio,
        "document_type": scan.document_type,
        "total_amount": float(scan.total_amount),
        "issue_date": scan.issue_date,
    }


@router.put("/{scan_id}", response_model=ScanResponse)
def update_scan_version(
    scan_id: str,
    update_in: ScanUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Actualiza los datos normalizados o el texto de un escaneo y crea una nueva entrada
    en SCAN_VERSIONS para mantener la trazabilidad completa.
    """
    scan = (
        db.query(Scan)
        .filter(
            Scan.id == scan_id,
            Scan.user_id == current_user.id,
            Scan.deleted_at.is_(None),
        )
        .first()
    )
    if not scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Escaneo no encontrado.",
        )

    latest_version_num = max(
        [v.version_number for v in scan.versions], default=1
    )
    new_version_num = latest_version_num + 1

    new_raw = (
        update_in.raw_text
        if update_in.raw_text is not None
        else scan.current_raw_text
    )
    norm_data = update_in.normalized_data

    scan.current_raw_text = new_raw
    scan.current_normalized_data = norm_data
    scan.document_type = norm_data.get("document_type", scan.document_type)
    scan.folio = norm_data.get("document_number", scan.folio)
    scan.issue_date = norm_data.get("issue_date", scan.issue_date)
    amounts = norm_data.get("amounts", {})
    if "total_amount" in amounts:
        scan.total_amount = _to_decimal_amount(amounts.get("total_amount"))

    new_version = ScanVersion(
        scan_id=scan.id,
        version_number=new_version_num,
        raw_text=new_raw,
        normalized_data=norm_data,
        modified_by=current_user.email,
    )
    db.add(new_version)
    db.commit()
    db.refresh(scan)

    return ScanResponse.model_validate(scan)


@router.get("/{scan_id}/export")
def export_single_scan_excel(
    scan_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scan = (
        db.query(Scan)
        .filter(
            Scan.id == scan_id,
            Scan.user_id == current_user.id,
            Scan.deleted_at.is_(None),
        )
        .first()
    )
    if not scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Escaneo no encontrado.",
        )

    base_url = str(request.base_url).rstrip("/")
    excel_bytes = generate_scans_excel([scan], base_url=base_url)
    folio_slug = (scan.folio or scan.id[:8]).replace("/", "-")

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="comprobante_{folio_slug}.xlsx"'
        },
    )


@router.post("/{scan_id}/restore", response_model=ScanResponse)
def restore_scan(
    scan_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Restaura un comprobante eliminado lógicamente (Soft-Delete) devolviéndolo al historial activo.
    """
    scan = (
        db.query(Scan)
        .filter(Scan.id == scan_id, Scan.user_id == current_user.id)
        .first()
    )
    if not scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Escaneo no encontrado.",
        )
    scan.deleted_at = None
    db.commit()
    db.refresh(scan)
    return ScanResponse.model_validate(scan)


@router.delete("/{scan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scan(
    scan_id: str,
    permanent: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Elimina un comprobante:
    - Por defecto (permanent=False): aplica Soft-Delete (deleted_at) moviéndolo a la Papelera Tributaria.
    - Si permanent=True: elimina definitivamente el registro y sus archivos WebP.
    """
    scan = (
        db.query(Scan)
        .filter(Scan.id == scan_id, Scan.user_id == current_user.id)
        .first()
    )
    if not scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Escaneo no encontrado.",
        )

    if permanent:
        _purge_scan_permanently(db, scan)
    else:
        scan.deleted_at = utc_now()

    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/files/{user_id}/{scan_id}/{filename}")
def get_scan_webp_file(
    user_id: str,
    scan_id: str,
    filename: str,
    expires: Optional[int] = Query(default=None),
    sig: Optional[str] = Query(default=None),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    """
    Sirve de forma protegida la imagen WebP optimizada o su miniatura verificando
    aislamiento multi-inquilino mediante:
    1. Cabecera estándar Authorization: Bearer <token>, o
    2. URL pre-firmada temporal con HMAC-SHA256 (?expires=...&sig=...) sin exponer jamás el JWT.
    """
    if filename not in ("document.webp", "thumbnail.webp"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Archivo no permitido.",
        )

    authorized_by_header = bool(current_user and current_user.id == user_id)
    authorized_by_sig = verify_signed_file_signature(
        user_id=user_id,
        scan_id=scan_id,
        filename=filename,
        expires=expires,
        sig=sig,
    )

    if current_user and current_user.id != user_id and not authorized_by_sig:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado a recursos de otro usuario.",
        )

    if not (authorized_by_header or authorized_by_sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Acceso no autorizado. Use cabecera Authorization o URL pre-firmada vigente.",
        )

    rel_path = f"{user_id}/{scan_id}/{filename}"

    # Si se utiliza S3 o Cloud Storage con URLs pre-firmadas
    if settings.STORAGE_BACKEND.upper() == "S3":
        presigned = storage_service.get_presigned_url(
            rel_path, expiration_seconds=settings.S3_PRESIGN_EXPIRATION_SECONDS
        )
        if presigned:
            return RedirectResponse(
                url=presigned, status_code=status.HTTP_307_TEMPORARY_REDIRECT
            )

    file_path = settings.UPLOADS_DIR / user_id / scan_id / filename
    if file_path.exists():
        return FileResponse(file_path, media_type="image/webp")

    # Si no existe en disco local, intentar traer de storage_service
    file_bytes = storage_service.get_bytes(rel_path)
    if file_bytes is not None:
        return Response(content=file_bytes, media_type="image/webp")

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Imagen no encontrada.",
    )
