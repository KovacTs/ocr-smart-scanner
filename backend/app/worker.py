import asyncio
import logging
import sys
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional
from arq import create_pool
from arq.connections import RedisSettings
from app.core.config import settings
from app.db.database import SessionLocal
import app.models.user  # noqa: F401 - Requerido para resolver relationship("User") en Scan
from app.models.scan import Scan, ScanVersion
from app.services.ocr_normalizer import (
    extract_raw_text_from_image,
    normalize_document_text,
)
from app.services.sii_barcode_service import (
    decode_barcodes_from_image,
    merge_sii_ted_with_ocr,
    parse_sii_ted_xml,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger("ocr_worker")


def _to_decimal_amount(raw_val: Any) -> Decimal:
    try:
        if raw_val is None or raw_val == "":
            return Decimal("0.00")
        return Decimal(str(raw_val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


async def process_ocr_scan_task(
    ctx: dict,
    scan_id: str,
    image_disk_path: str,
    raw_text_hint: Optional[str] = None,
    sii_ted_xml: Optional[str] = None,
) -> dict:
    """
    Tarea asíncrona de Arq/Redis:
    Ejecuta el procesamiento intensivo de CPU/GPU (Decodificación PDF417, RapidOCR, LLM)
    fuera del hilo HTTP principal de FastAPI para responder en < 200 ms.
    """
    t_start = time.perf_counter()
    short_id = scan_id[:8]
    logger.info(
        f"🚀 [Worker Scan #{short_id}] Iniciando procesamiento OCR en segundo plano ({image_disk_path})"
    )
    db = SessionLocal()
    try:
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            logger.error(f"❌ [Worker Scan #{short_id}] Escaneo {scan_id} no encontrado en BD.")
            return {"error": "SCAN_NOT_FOUND"}

        t_ted = time.perf_counter()
        ted_from_client = parse_sii_ted_xml(sii_ted_xml) if sii_ted_xml else None
        ted_data = ted_from_client or decode_barcodes_from_image(image_disk_path)
        ted_ms = int((time.perf_counter() - t_ted) * 1000)
        if ted_data:
            logger.info(
                f"📊 [Worker Scan #{short_id}] Código SII detectado ({ted_data.get('source')}) en {ted_ms}ms -> "
                f"Folio={ted_data.get('document_number')}, Total=${ted_data.get('amounts', {}).get('total_amount')}"
            )
        else:
            logger.info(f"📊 [Worker Scan #{short_id}] Sin código de barras SII legible ({ted_ms}ms). Usando OCR visual.")

        t_ocr = time.perf_counter()
        raw_text = extract_raw_text_from_image(
            image_path=image_disk_path,
            raw_text_hint=raw_text_hint,
        )
        ocr_ms = int((time.perf_counter() - t_ocr) * 1000)
        line_count = len([ln for ln in raw_text.splitlines() if ln.strip()])
        logger.info(
            f"🔍 [Worker Scan #{short_id}] OCR Visual completado en {ocr_ms}ms ({line_count} líneas extraídas)"
        )

        has_full_ted = bool(ted_data and ted_data.get("source") == "SII_TED_PDF417")
        t_norm = time.perf_counter()
        ocr_normalized = normalize_document_text(
            raw_text,
            image_path=None if raw_text_hint else image_disk_path,
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

        # Actualiza el escaneo y marca como COMPLETED
        scan.status = "COMPLETED"
        scan.document_type = normalized_data.get("document_type", "FACTURA_ELECTRONICA")
        scan.folio = normalized_data.get("document_number", "S/N")
        scan.current_raw_text = raw_text
        scan.current_normalized_data = normalized_data
        scan.issue_date = normalized_data.get("issue_date")
        scan.total_amount = total_amount_dec

        if has_full_ted:
            mod_by = "SII_TED_PDF417"
        elif normalized_data.get("llm_refined"):
            mod_by = "OCR_LLM_HYBRID"
        else:
            mod_by = "OCR_AUTOMATIC"

        # Registra la versión inicial
        existing_version = (
            db.query(ScanVersion)
            .filter(ScanVersion.scan_id == scan_id, ScanVersion.version_number == 1)
            .first()
        )
        if not existing_version:
            version_1 = ScanVersion(
                scan_id=scan_id,
                version_number=1,
                raw_text=raw_text,
                normalized_data=normalized_data,
                modified_by=mod_by,
            )
            db.add(version_1)

        db.commit()
        total_ms = int((time.perf_counter() - t_start) * 1000)
        issuer_info = normalized_data.get("issuer", {})
        logger.info(
            f"✅ [Worker Scan #{short_id}] COMPLETADO en {total_ms}ms (Norm/LLM: {norm_ms}ms, Método: {mod_by}) | "
            f"Tipo={scan.document_type} | Folio={scan.folio} | "
            f"RUT={issuer_info.get('tax_id') or 'S/RUT'} ({issuer_info.get('legal_name')}) | "
            f"Total=${scan.total_amount}"
        )
        return {
            "status": "COMPLETED",
            "scan_id": scan_id,
            "folio": scan.folio,
            "total_amount": float(scan.total_amount),
        }
    except Exception as e:
        logger.exception(f"❌ [Worker Scan #{short_id}] Error en procesamiento: {e}")
        try:
            scan = db.query(Scan).filter(Scan.id == scan_id).first()
            if scan:
                scan.status = "FAILED"
                scan.folio = "ERROR_OCR"
                db.commit()
        except Exception as db_err:
            logger.error(f"❌ [Worker Scan #{short_id}] No se pudo actualizar estado FAILED: {db_err}")
        return {"status": "FAILED", "scan_id": scan_id, "error": str(e)}
    finally:
        db.close()


async def recover_pending_scans_on_startup(ctx: dict) -> None:
    """
    Al iniciar el worker, revisa si quedaron escaneos atrapados en estado PROCESSING
    (por ejemplo tras un reinicio de contenedor) y los procesa automáticamente.
    """
    db = SessionLocal()
    try:
        pending_scans = (
            db.query(Scan)
            .filter(Scan.status == "PROCESSING")
            .order_by(Scan.created_at.asc())
            .all()
        )
        if not pending_scans:
            return
        logger.info(
            f"🔄 [Worker Recovery] Se encontraron {len(pending_scans)} escaneo(s) pendientes en cola. Procesando..."
        )
        items = [(s.id, s.user_id) for s in pending_scans]
    except Exception as e:
        logger.error(f"❌ [Worker Recovery] Error consultando escaneos pendientes: {e}")
        return
    finally:
        db.close()

    for scan_id, user_id in items:
        disk_path = settings.UPLOADS_DIR / user_id / scan_id / "document.webp"
        if disk_path.exists():
            await process_ocr_scan_task(ctx, scan_id=scan_id, image_disk_path=str(disk_path))
        else:
            logger.warning(
                f"⚠️ [Worker Recovery] Archivo no encontrado para Scan #{scan_id[:8]} ({disk_path}). Marcando FAILED."
            )
            db2 = SessionLocal()
            try:
                sc = db2.query(Scan).filter(Scan.id == scan_id).first()
                if sc:
                    sc.status = "FAILED"
                    sc.folio = "ARCHIVO_NO_ENCONTRADO"
                    db2.commit()
            finally:
                db2.close()


class WorkerSettings:
    functions = [process_ocr_scan_task]
    on_startup = recover_pending_scans_on_startup
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 4
    poll_delay = 0.5


if __name__ == "__main__":
    from arq.worker import run_worker

    logger.info("Iniciando Arq Worker para OCR Smart Scanner...")
    run_worker(WorkerSettings)

