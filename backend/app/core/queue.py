import asyncio
import logging
from typing import Optional
from arq import create_pool
from arq.connections import RedisSettings
from app.core.config import settings
from app.worker import process_ocr_scan_task

logger = logging.getLogger("task_queue")


async def enqueue_scan_job(
    scan_id: str,
    image_disk_path: str,
    raw_text_hint: Optional[str] = None,
    sii_ted_xml: Optional[str] = None,
) -> bool:
    """
    Despacha la tarea de procesamiento OCR a Redis + Arq si está habilitado y disponible.
    Si Redis no está disponible o falla la conexión, ejecuta la tarea en segundo plano
    de forma local (asyncio task) sin interrumpir la respuesta del servidor.
    """
    if settings.USE_ASYNC_QUEUE:
        try:
            redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
            redis_pool = await asyncio.wait_for(
                create_pool(redis_settings), timeout=1.5
            )
            await redis_pool.enqueue_job(
                "process_ocr_scan_task",
                scan_id,
                image_disk_path,
                raw_text_hint,
                sii_ted_xml,
            )
            await redis_pool.aclose()
            logger.info(f"[Queue] Trabajo encolado exitosamente en Redis para Scan #{scan_id}")
            return True
        except Exception as e:
            logger.warning(
                f"[Queue] No se pudo encolar en Redis ({e}). Ejecutando worker local de respaldo."
            )

    # Fallback local con asyncio task en segundo plano
    asyncio.create_task(
        process_ocr_scan_task(
            ctx={},
            scan_id=scan_id,
            image_disk_path=image_disk_path,
            raw_text_hint=raw_text_hint,
            sii_ted_xml=sii_ted_xml,
        )
    )
    logger.info(f"[Queue] Trabajo despachado en segundo plano local para Scan #{scan_id}")
    return True
