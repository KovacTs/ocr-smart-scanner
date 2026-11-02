import shutil
import uuid
from typing import List, Optional
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
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.models.scan import Scan, ScanVersion
from app.models.user import User
from app.schemas.scan import (
    BatchDeleteRequest,
    BatchExportRequest,
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

router = APIRouter(prefix="/scans", tags=["Escaneos, Enfoque, Normalización y Excel"])


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
    image_bytes = await file.read()
    result = analyze_focus_and_roi(image_bytes, threshold=threshold)
    return FocusCheckResponse(**result)


@router.post("", response_model=ScanResponse, status_code=status.HTTP_201_CREATED)
async def create_scan(
    file: UploadFile = File(...),
    raw_text_hint: Optional[str] = Form(default=None),
    sii_ted_xml: Optional[str] = Form(default=None),
    force_capture: bool = Form(default=False),
    blur_threshold: Optional[float] = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Pipeline completo de captura de documento:
    1. Valida el nivel de enfoque de la imagen (rechaza fotos borrosas salvo force_capture=True o Timbre SII leído).
    2. Normaliza resolución (máx 2400px) y comprime a WebP (82% calidad) + Miniatura WebP (400px).
    3. Decodifica Timbre Electrónico SII (PDF417 <TED>) desde cliente o desde la imagen con zxing-cpp.
    4. Ejecuta OCR visual y fusiona con prioridad criptográfica del Timbre SII.
    5. Guarda el escaneo y registra la Versión #1 en el historial del usuario.
    """
    image_bytes = await file.read()
    focus_info = analyze_focus_and_roi(image_bytes, threshold=blur_threshold)
    ted_from_client = parse_sii_ted_xml(sii_ted_xml) if sii_ted_xml else None

    if not force_capture and not focus_info["is_focused"] and ted_from_client is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "IMAGE_OUT_OF_FOCUS",
                "message": (
                    f"La imagen está desenfocada (Score: {focus_info['blur_score']} < "
                    f"Umbral: {focus_info['threshold']}). Mantenga el dispositivo estable."
                ),
                "focus_analysis": focus_info,
            },
        )

    scan_id = str(uuid.uuid4())
    storage_info = optimize_and_save_webp(
        image_bytes=image_bytes,
        user_id=current_user.id,
        scan_id=scan_id,
    )

    ted_data = ted_from_client or decode_barcodes_from_image(
        storage_info["main_disk_path"]
    )

    raw_text = extract_raw_text_from_image(
        image_path=storage_info["main_disk_path"],
        raw_text_hint=raw_text_hint,
    )
    has_full_ted = bool(ted_data and ted_data.get("source") == "SII_TED_PDF417")
    ocr_normalized = normalize_document_text(
        raw_text,
        image_path=None if raw_text_hint else storage_info["main_disk_path"],
        use_llm_fallback=not has_full_ted,
    )
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
    total_amount = float(amounts.get("total_amount", 0.0) or 0.0)

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
        total_amount=total_amount,
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

    return ScanResponse.model_validate(scan)


@router.get("", response_model=List[ScanResponse])
def list_user_scans(
    search: Optional[str] = Query(default=None),
    document_type: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Scan).filter(Scan.user_id == current_user.id)

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

    scans = query.order_by(Scan.created_at.desc()).all()
    return [ScanResponse.model_validate(s) for s in scans]


@router.post("/export-batch")
def export_scans_batch_excel(
    req: BatchExportRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Exporta un lote de escaneos filtrados o seleccionados a una planilla Excel (.xlsx)
    con formato contable y fórmulas automáticas.
    """
    query = db.query(Scan).filter(Scan.user_id == current_user.id)

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
    Elimina uno o más escaneos seleccionados por el usuario, borrando tanto los
    registros de la base de datos (y sus versiones) como sus archivos WebP en disco.
    """
    if not req.scan_ids:
        return {"deleted_count": 0}

    scans = (
        db.query(Scan)
        .filter(Scan.user_id == current_user.id, Scan.id.in_(req.scan_ids))
        .all()
    )
    deleted_count = 0
    for scan in scans:
        scan_dir = settings.UPLOADS_DIR / current_user.id / scan.id
        if scan_dir.exists():
            shutil.rmtree(scan_dir, ignore_errors=True)
        db.delete(scan)
        deleted_count += 1

    db.commit()
    return {"deleted_count": deleted_count}


@router.get("/{scan_id}", response_model=ScanResponse)
def get_scan_detail(
    scan_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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
    return ScanResponse.model_validate(scan)


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
        .filter(Scan.id == scan_id, Scan.user_id == current_user.id)
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
        scan.total_amount = float(amounts.get("total_amount") or 0.0)

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
        .filter(Scan.id == scan_id, Scan.user_id == current_user.id)
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


@router.delete("/{scan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scan(
    scan_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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

    scan_dir = settings.UPLOADS_DIR / current_user.id / scan.id
    if scan_dir.exists():
        shutil.rmtree(scan_dir, ignore_errors=True)

    db.delete(scan)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/files/{user_id}/{scan_id}/{filename}")
def get_scan_webp_file(
    user_id: str,
    scan_id: str,
    filename: str,
    current_user: User = Depends(get_current_user),
):
    """
    Sirve de forma protegida la imagen WebP optimizada o su miniatura verificando
    que pertenezca al usuario autenticado (Aislamiento Multi-inquilino).
    """
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado a recursos de otro usuario.",
        )
    if filename not in ("document.webp", "thumbnail.webp"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Archivo no permitido.",
        )

    file_path = settings.UPLOADS_DIR / user_id / scan_id / filename
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Imagen no encontrada.",
        )

    return FileResponse(file_path, media_type="image/webp")
