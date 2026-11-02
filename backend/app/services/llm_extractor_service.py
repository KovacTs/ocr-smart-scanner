import base64
import json
import os
import re
from typing import Any, Optional
import httpx

from app.core.config import settings
from app.services.ocr_normalizer import (
    CHILE_KNOWN_ISSUERS,
    PAYMENT_PROCESSOR_KEYWORDS,
    format_rut,
    parse_iso_date,
    parse_monetary_value,
    validate_rut_mod11,
)

ALLOWED_DOCUMENT_TYPES = {
    "FACTURA_ELECTRONICA",
    "FACTURA_EXENTA",
    "BOLETA_ELECTRONICA",
    "BOLETA_EXENTA",
    "BOLETA_HONORARIOS",
    "GUIA_DESPACHO",
    "NOTA_CREDITO",
    "NOTA_DEBITO",
    "COMPROBANTE_GENERAL",
}

SYSTEM_PROMPT_CHILE_SII = """Eres un auditor tributario experto en Documentos Tributarios Electrónicos (DTE) del SII de Chile (Boletas Electrónicas, Facturas Electrónicas, Facturas Exentas y Boletas de Honorarios).
Tu tarea es analizar el texto crudo extraído por OCR (y la imagen adjunta si está disponible) de una boleta o factura chilena y devolver EXCLUSIVAMENTE un objeto JSON válido con la siguiente estructura exacta:

{
  "document_type": "BOLETA_ELECTRONICA | FACTURA_ELECTRONICA | FACTURA_EXENTA | BOLETA_HONORARIOS | COMPROBANTE_GENERAL",
  "document_number": "string con solo los dígitos del Folio/Número de boleta o factura, o S/N",
  "issue_date": "YYYY-MM-DD",
  "issuer": {
    "tax_id": "XX.XXX.XXX-X",
    "legal_name": "Razón Social o Nombre del Comercio Emisor (ej. HIPER LIMITADA (LIDER), CENCOSUD RETAIL S.A., DECATHLON CHILE SPA)",
    "address": "Dirección de la sucursal o casa matriz"
  },
  "receiver": {
    "tax_id": "XX.XXX.XXX-X o vacío",
    "legal_name": "Nombre del cliente receptor o CLIENTE GENERAL"
  },
  "amounts": {
    "net_amount": 0,
    "tax_amount": 0,
    "exempt_amount": 0,
    "total_amount": 0,
    "currency": "CLP"
  }
}

Reglas estrictas de negocio para Chile:
1. FOLIO (document_number):
   - En supermercados (como Lider/Hiper Limitada), aparece abreviado como "Bol. Electronica: 002640601308" o con errores ópticos como "3ol. Electronical 002640601308" o "BOLETA ELECTRONICA N9 3312694425".
   - NUNCA confundas el número de Caja ("Caja: 0014"), Local ("Local: 2001") o Tienda con el Folio.
   - Si aparece una cadena numérica de 30 o 31 dígitos del timbre SII al pie (ej. "202610094004208295833126944251"), los 10 dígitos anteriores al último dígito verificador ("3312694425") corresponden al Folio.
2. COMERCIO EMISOR (issuer.legal_name y issuer.tax_id):
   - El comercio emisor SIEMPRE está en el encabezado superior (ej. "HIPER LIMITADA - RUT: 76.134.941-4", "CENCOSUD RETAIL S.A.", "DECATHLON CHILE SPA").
   - NUNCA uses empresas procesadoras de tarjetas/becas del pie de página (como "Pluxee Chile S.A.", "JUNAEB", "Sodexo", "Edenred", "Transbank") como el emisor.
3. MONTOS (amounts):
   - En Chile el IVA es del 19%. "TOTAL AFECTO" o "MONTO AFECTO" es sinónimo de "net_amount" (Monto Neto).
   - Debe cumplirse: net_amount + tax_amount + exempt_amount = total_amount.
   - Devuelve números enteros sin puntos de miles (ej. 42241, no 42.241).
"""


def should_invoke_llm(normalized_data: dict[str, Any]) -> bool:
    """
    Determina si el resultado del normalizador heurístico requiere refinamiento
    semántico con el LLM (cuando falta el Folio, el Comercio, el RUT, el Total o la confianza es baja).
    """
    if settings.LLM_PROVIDER.upper() == "DISABLED":
        return False

    folio = str(normalized_data.get("document_number") or "").strip()
    issuer = normalized_data.get("issuer") or {}
    issuer_rut = str(issuer.get("tax_id") or "").strip()
    issuer_name = str(issuer.get("legal_name") or "").strip()
    amounts = normalized_data.get("amounts") or {}
    total = float(amounts.get("total_amount") or 0.0)
    confidence = float(normalized_data.get("confidence_score") or 0.0)

    if not folio or folio == "S/N":
        return True
    if not issuer_name or issuer_name == "EMISOR NO IDENTIFICADO":
        return True
    if not issuer_rut or not validate_rut_mod11(issuer_rut):
        return True
    if total <= 0:
        return True
    if confidence < 0.90:
        return True
    return False


def _extract_json_object(raw_response: str) -> Optional[dict[str, Any]]:
    """
    Extrae y parsea de forma segura el bloque JSON devuelto por el LLM,
    incluso si viene envuelto en bloques markdown ```json ... ```.
    """
    if not raw_response:
        return None
    cleaned = raw_response.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        data = json.loads(cleaned[start : end + 1])
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _call_ollama_api(raw_text: str) -> Optional[dict[str, Any]]:
    """
    Invoca al servidor local Ollama (ej. qwen2.5:3b o llama3.2:3b) en modo JSON estructurado.
    Usa un timeout de conexión rápido (0.6s) para no bloquear si Ollama está apagado.
    """
    url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/generate"
    prompt = (
        f"Analiza el siguiente texto OCR de un comprobante tributario chileno y extrae el JSON:\n\n"
        f"--- TEXTO OCR ---\n{raw_text}\n--- FIN TEXTO OCR ---"
    )
    payload = {
        "model": settings.OLLAMA_MODEL,
        "system": SYSTEM_PROMPT_CHILE_SII,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 512,
        },
    }
    timeout = httpx.Timeout(settings.LLM_TIMEOUT_SECONDS, connect=0.6)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                return None
            body = resp.json()
            response_text = body.get("response", "")
            return _extract_json_object(response_text)
    except Exception:
        return None


def _call_gemini_api(
    raw_text: str, image_path: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """
    Invoca a Google Gemini Flash API (Multimodal: Imagen WebP + Texto OCR) con salida JSON estructurada.
    Soporta cascada automática de modelos Flash en caso de alta demanda temporal (503 / timeout).
    """
    api_key = settings.GEMINI_API_KEY.strip()
    if not api_key:
        return None

    candidate_models = [settings.GEMINI_MODEL]
    for fallback in [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-flash-lite-latest",
    ]:
        if fallback not in candidate_models:
            candidate_models.append(fallback)

    parts: list[dict[str, Any]] = [
        {
            "text": (
                f"{SYSTEM_PROMPT_CHILE_SII}\n\n"
                f"--- TEXTO OCR EXTRAÍDO ---\n{raw_text}\n--- FIN TEXTO OCR ---"
            )
        }
    ]

    if image_path and os.path.exists(image_path):
        try:
            with open(image_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")
            mime_type = (
                "image/webp"
                if image_path.lower().endswith(".webp")
                else "image/jpeg"
            )
            parts.append(
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": img_b64,
                    }
                }
            )
        except Exception:
            pass

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
        },
    }

    timeout = httpx.Timeout(settings.LLM_TIMEOUT_SECONDS, connect=3.0)
    for model_name in candidate_models:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent?key={api_key}"
        )
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=payload)
                if resp.status_code != 200:
                    continue
                body = resp.json()
                candidates = body.get("candidates") or []
                if not candidates:
                    continue
                content_parts = candidates[0].get("content", {}).get("parts") or []
                if not content_parts:
                    continue
                text_out = content_parts[0].get("text", "")
                parsed = _extract_json_object(text_out)
                if parsed:
                    parsed["_provider"] = f"GEMINI ({model_name})"
                    return parsed
        except Exception:
            continue

    return None


def extract_structured_data_with_llm(
    raw_text: str, image_path: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """
    Ejecuta la extracción semántica seleccionando el proveedor según configuración:
    - GEMINI: usa Gemini Multimodal API.
    - OLLAMA: usa Ollama Local.
    - AUTO: usa Gemini si hay GEMINI_API_KEY configurada; de lo contrario usa Ollama Local.
    """
    provider = settings.LLM_PROVIDER.upper().strip()
    if provider == "DISABLED":
        return None

    if provider == "GEMINI":
        res = _call_gemini_api(raw_text, image_path=image_path)
        if res:
            res["_provider"] = f"GEMINI ({settings.GEMINI_MODEL})"
        return res

    if provider == "OLLAMA":
        res = _call_ollama_api(raw_text)
        if res:
            res["_provider"] = f"OLLAMA ({settings.OLLAMA_MODEL})"
        return res

    # Modo AUTO
    if settings.GEMINI_API_KEY.strip():
        res = _call_gemini_api(raw_text, image_path=image_path)
        if res:
            res["_provider"] = f"GEMINI ({settings.GEMINI_MODEL})"
            return res

    res = _call_ollama_api(raw_text)
    if res:
        res["_provider"] = f"OLLAMA ({settings.OLLAMA_MODEL})"
    return res


def _to_number(val: Any) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        return parse_monetary_value(val)
    return 0.0


def sanitize_and_merge_llm_output(
    llm_raw: Optional[dict[str, Any]], heuristic_data: dict[str, Any]
) -> dict[str, Any]:
    """
    Valida y fusiona la respuesta del LLM con los datos heurísticos:
    - Asegura que ningún RUT validado en Módulo 11 sea reemplazado por uno inválido.
    - Filtra procesadores de pago (Pluxee/JUNAEB/Transbank) del nombre del comercio.
    - Reconcilia contablemente Neto + IVA (19%) + Exento = Total.
    """
    if not llm_raw or not isinstance(llm_raw, dict):
        return heuristic_data

    merged = json.loads(json.dumps(heuristic_data))

    # 1. Tipo de documento
    llm_doc_type = str(llm_raw.get("document_type") or "").strip().upper()
    if llm_doc_type in ALLOWED_DOCUMENT_TYPES:
        if (
            merged.get("document_type") == "COMPROBANTE_GENERAL"
            or llm_doc_type != "COMPROBANTE_GENERAL"
        ):
            merged["document_type"] = llm_doc_type

    # 2. Folio / Número de documento
    llm_folio_raw = str(llm_raw.get("document_number") or "").strip()
    llm_folio_digits = re.sub(r"\D", "", llm_folio_raw)
    if len(llm_folio_digits) >= 4:
        current_folio = str(merged.get("document_number") or "S/N")
        if current_folio == "S/N" or len(current_folio) < 4:
            merged["document_number"] = llm_folio_digits

    # 3. Fecha de emisión ISO 8601
    llm_date = str(llm_raw.get("issue_date") or "").strip()
    if re.match(r"^20\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$", llm_date):
        merged["issue_date"] = llm_date
    elif llm_date:
        parsed_d = parse_iso_date(llm_date)
        if parsed_d:
            merged["issue_date"] = parsed_d

    # 4. Emisor (RUT, Razón Social y Dirección)
    llm_issuer = llm_raw.get("issuer") if isinstance(llm_raw.get("issuer"), dict) else {}
    cur_issuer = merged.setdefault("issuer", {"tax_id": "", "legal_name": "", "address": ""})

    llm_rut_raw = str(llm_issuer.get("tax_id") or "").strip()
    if llm_rut_raw:
        fmt_rut = format_rut(llm_rut_raw)
        if validate_rut_mod11(fmt_rut):
            if not cur_issuer.get("tax_id") or not validate_rut_mod11(cur_issuer["tax_id"]):
                cur_issuer["tax_id"] = fmt_rut

    llm_name = str(llm_issuer.get("legal_name") or "").strip()
    if (
        llm_name
        and llm_name.upper() != "EMISOR NO IDENTIFICADO"
        and not any(pk in llm_name.upper() for pk in PAYMENT_PROCESSOR_KEYWORDS)
    ):
        if (
            not cur_issuer.get("legal_name")
            or cur_issuer["legal_name"] == "EMISOR NO IDENTIFICADO"
        ):
            cur_issuer["legal_name"] = llm_name

    # Si el RUT pertenece al directorio oficial chileno, estandarizar el nombre del comercio
    if cur_issuer.get("tax_id") in CHILE_KNOWN_ISSUERS:
        cur_issuer["legal_name"] = CHILE_KNOWN_ISSUERS[cur_issuer["tax_id"]]

    llm_addr = str(llm_issuer.get("address") or "").strip()
    if llm_addr and not cur_issuer.get("address"):
        cur_issuer["address"] = llm_addr

    # 5. Receptor
    llm_receiver = (
        llm_raw.get("receiver") if isinstance(llm_raw.get("receiver"), dict) else {}
    )
    cur_receiver = merged.setdefault(
        "receiver", {"tax_id": "", "legal_name": "CLIENTE GENERAL", "address": ""}
    )
    llm_rec_rut = str(llm_receiver.get("tax_id") or "").strip()
    if llm_rec_rut:
        fmt_rec_rut = format_rut(llm_rec_rut)
        if validate_rut_mod11(fmt_rec_rut) and fmt_rec_rut != "66.666.666-6":
            if not cur_receiver.get("tax_id"):
                cur_receiver["tax_id"] = fmt_rec_rut

    llm_rec_name = str(llm_receiver.get("legal_name") or "").strip()
    if (
        llm_rec_name
        and llm_rec_name.upper() not in ("", "CLIENTE GENERAL", "CLIENTE", "BOLETA")
        and cur_receiver.get("legal_name") in ("", "CLIENTE GENERAL")
    ):
        cur_receiver["legal_name"] = llm_rec_name

    # 6. Montos Monetarios y Reconciliación Contable
    llm_amounts = (
        llm_raw.get("amounts") if isinstance(llm_raw.get("amounts"), dict) else {}
    )
    cur_amounts = merged.setdefault(
        "amounts",
        {
            "net_amount": 0.0,
            "tax_amount": 0.0,
            "exempt_amount": 0.0,
            "total_amount": 0.0,
            "currency": "CLP",
        },
    )

    llm_total = _to_number(llm_amounts.get("total_amount"))
    llm_net = _to_number(llm_amounts.get("net_amount"))
    llm_tax = _to_number(llm_amounts.get("tax_amount"))
    llm_exempt = _to_number(llm_amounts.get("exempt_amount"))

    if float(cur_amounts.get("total_amount") or 0.0) <= 0 and llm_total > 0:
        cur_amounts["total_amount"] = llm_total
        if llm_net > 0:
            cur_amounts["net_amount"] = llm_net
        if llm_tax > 0:
            cur_amounts["tax_amount"] = llm_tax
        if llm_exempt > 0:
            cur_amounts["exempt_amount"] = llm_exempt

        total_val = cur_amounts["total_amount"]
        if (
            cur_amounts["net_amount"] == 0
            or abs(
                (
                    cur_amounts["net_amount"]
                    + cur_amounts["tax_amount"]
                    + cur_amounts["exempt_amount"]
                )
                - total_val
            )
            > 5
        ):
            if merged.get("document_type") not in ("FACTURA_EXENTA", "BOLETA_EXENTA"):
                expected_net = round((total_val - cur_amounts["exempt_amount"]) / 1.19, 0)
                cur_amounts["net_amount"] = expected_net
                cur_amounts["tax_amount"] = round(
                    total_val - cur_amounts["exempt_amount"] - expected_net, 0
                )
            else:
                cur_amounts["exempt_amount"] = total_val

    # Recalcular score de confianza tras fusión con LLM
    checks = [
        bool(merged.get("document_number") and merged.get("document_number") != "S/N"),
        bool(merged.get("issue_date")),
        bool(
            cur_issuer.get("tax_id") and validate_rut_mod11(cur_issuer["tax_id"])
        ),
        bool(
            cur_issuer.get("legal_name")
            and cur_issuer.get("legal_name") != "EMISOR NO IDENTIFICADO"
        ),
        float(cur_amounts.get("total_amount") or 0.0) > 0,
    ]
    merged["confidence_score"] = round(0.60 + (sum(checks) / len(checks)) * 0.39, 2)
    merged["llm_refined"] = True
    merged["llm_provider"] = llm_raw.get("_provider", settings.LLM_PROVIDER)
    return merged


def refine_normalization_with_llm(
    raw_text: str,
    heuristic_data: dict[str, Any],
    image_path: Optional[str] = None,
    force_llm: bool = False,
) -> dict[str, Any]:
    """
    Orquestador de la Capa 3 (Extractor Semántico LLM/VLM):
    Si los datos heurísticos están incompletos (o force_llm=True), consulta al modelo
    configurado (Ollama Local o Gemini Flash) y fusiona los resultados validados.
    """
    if not force_llm and not should_invoke_llm(heuristic_data):
        return heuristic_data

    llm_result = extract_structured_data_with_llm(raw_text, image_path=image_path)
    if not llm_result:
        return heuristic_data

    return sanitize_and_merge_llm_output(llm_result, heuristic_data)
