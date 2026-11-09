import asyncio
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional
from arq import create_pool
from arq.connections import RedisSettings
from app.core.config import settings
from app.db.database import SessionLocal
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

logger = logging.getLogger("ocr_worker")
logging.basicConfig(level=logging.INFO)


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
    logger.info(f"[Worker] Iniciando procesamiento en segundo plano para Scan #{scan_id}")
    db = SessionLocal()
    try:
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            logger.error(f"[Worker] Escaneo {scan_id} no encontrado en base de datos.")
            return {"error": "SCAN_NOT_FOUND"}

        ted_from_client = parse_sii_ted_xml(sii_ted_xml) if sii_ted_xml else None
        ted_data = ted_from_client or decode_barcodes_from_image(image_disk_path)

        raw_text = extract_raw_text_from_image(
            image_path=image_disk_path,
            raw_text_hint=raw_text_hint,
        )
        has_full_ted = bool(ted_data and ted_data.get("source") == "SII_TED_PDF417")
        ocr_normalized = normalize_document_text(
            raw_text,
            image_path=None if raw_text_hint else image_disk_path,
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
        logger.info(
            f"[Worker] Procesamiento completado para Scan #{scan_id}: Folio={scan.folio}, Total=${scan.total_amount}"
        )
        return {
            "status": "COMPLETED",
            "scan_id": scan_id,
            "folio": scan.folio,
            "total_amount": float(scan.total_amount),
        }
    except Exception as e:
        logger.exception(f"[Worker] Error en procesamiento de Scan #{scan_id}: {e}")
        try:
            scan = db.query(Scan).filter(Scan.id == scan_id).first()
            if scan:
                scan.status = "FAILED"
                db.commit()
        except Exception:
            pass
        return {"status": "FAILED", "scan_id": scan_id, "error": str(e)}
    finally:
        db.close()


class WorkerSettings:
    functions = [process_ocr_scan_task]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 4
    poll_delay = 0.5


if __name__ == "__main__":
    from arq.worker import run_worker

    logger.info("Iniciando Arq Worker para OCR Smart Scanner...")
    run_worker(WorkerSettings)
