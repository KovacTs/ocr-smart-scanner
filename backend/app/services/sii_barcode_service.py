import re
from typing import Any, Optional
import cv2
import numpy as np

from app.services.ocr_normalizer import (
    CHILE_KNOWN_ISSUERS,
    format_rut,
    parse_iso_date,
    validate_rut_mod11,
)

try:
    import zxingcpp
except ImportError:
    zxingcpp = None


SII_DTE_TYPE_MAP: dict[str, str] = {
    "33": "FACTURA_ELECTRONICA",
    "34": "FACTURA_EXENTA",
    "39": "BOLETA_ELECTRONICA",
    "41": "BOLETA_EXENTA",
    "46": "FACTURA_ELECTRONICA",
    "52": "GUIA_DESPACHO",
    "56": "NOTA_DEBITO",
    "61": "NOTA_CREDITO",
}


def _extract_xml_tag(xml_text: str, tag: str) -> str:
    """
    Extrae el contenido de una etiqueta XML <TAG>...</TAG> de forma tolerante
    a codificaciones ISO-8859-1, saltos de línea o espacios.
    """
    m = re.search(rf"(?is)<{tag}\b[^>]*>(.*?)</{tag}>", xml_text)
    return m.group(1).strip() if m else ""


def parse_sii_ted_xml(raw_payload: str) -> Optional[dict[str, Any]]:
    """
    Parsea el XML del Timbre Electrónico del SII (<TED version="1.0">...<DD>...</DD></TED>)
    contenido en el código bidimensional PDF417 de facturas y boletas chilenas.
    Retorna un diccionario estructurado con 100% de exactitud tributaria o None si no es un TED válido.
    """
    if not raw_payload or not raw_payload.strip():
        return None

    text = raw_payload.strip()

    # Verificar si contiene estructura de Timbre Electrónico SII (<TED> o <DD> con <RE> y <F>)
    if not (
        "<TED" in text.upper()
        or ("<DD" in text.upper() and "<RE>" in text.upper() and "<F>" in text.upper())
    ):
        # También soportar códigos de barra 1D SII/Retail de 30-31 dígitos (YYYYMMDD + Local/Caja + 10 dígitos Folio + DV)
        m_1d = re.search(r"^\s*(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{11,12}(\d{10})\d\s*$", text)
        if m_1d:
            return {
                "source": "BARCODE_1D_SII",
                "document_type": "BOLETA_ELECTRONICA",
                "document_number": m_1d.group(4),
                "issue_date": f"{m_1d.group(1)}-{m_1d.group(2)}-{m_1d.group(3)}",
                "issuer": {"tax_id": "", "legal_name": "", "address": ""},
                "receiver": {"tax_id": "", "legal_name": ""},
                "amounts": {
                    "net_amount": 0.0,
                    "tax_amount": 0.0,
                    "exempt_amount": 0.0,
                    "total_amount": 0.0,
                    "currency": "CLP",
                },
                "confidence_score": 0.95,
            }
        return None

    dd_block = _extract_xml_tag(text, "DD") or text
    caf_block = _extract_xml_tag(dd_block, "CAF") or text
    da_block = _extract_xml_tag(caf_block, "DA") or caf_block

    raw_re = _extract_xml_tag(dd_block, "RE") or _extract_xml_tag(da_block, "RE")
    raw_td = _extract_xml_tag(dd_block, "TD") or _extract_xml_tag(da_block, "TD")
    raw_folio = _extract_xml_tag(dd_block, "F")
    raw_fe = _extract_xml_tag(dd_block, "FE")
    raw_rr = _extract_xml_tag(dd_block, "RR")
    raw_rsr = _extract_xml_tag(dd_block, "RSR")
    raw_mnt = _extract_xml_tag(dd_block, "MNT")
    raw_it1 = _extract_xml_tag(dd_block, "IT1")
    raw_rs = _extract_xml_tag(da_block, "RS")

    if not raw_folio and not raw_re and not raw_mnt:
        return None

    issuer_rut = format_rut(raw_re) if raw_re else ""
    receiver_rut = format_rut(raw_rr) if raw_rr else ""
    # En boletas electrónicas chilenas, 66.666.666-6 es el RUT genérico SII para consumidor final
    if receiver_rut == "66.666.666-6":
        receiver_rut = ""

    doc_type = SII_DTE_TYPE_MAP.get(raw_td.strip(), "BOLETA_ELECTRONICA")
    folio = re.sub(r"\D", "", raw_folio) or raw_folio.strip()
    issue_date = parse_iso_date(raw_fe) if raw_fe else ""

    issuer_name = raw_rs.strip()
    if issuer_rut in CHILE_KNOWN_ISSUERS:
        issuer_name = CHILE_KNOWN_ISSUERS[issuer_rut]

    receiver_name = raw_rsr.strip()
    if not receiver_name or receiver_name.upper() in (
        "CONTADO",
        "BOLETA",
        "CONSUMIDOR FINAL",
        "SIN NOMBRE",
        "66666666-6",
    ):
        receiver_name = "CLIENTE GENERAL"

    try:
        total_amount = float(re.sub(r"[^\d.]", "", raw_mnt)) if raw_mnt else 0.0
    except ValueError:
        total_amount = 0.0

    net_amount = 0.0
    tax_amount = 0.0
    exempt_amount = 0.0

    if total_amount > 0:
        if doc_type in ("FACTURA_EXENTA", "BOLETA_EXENTA"):
            exempt_amount = total_amount
        else:
            net_amount = round(total_amount / 1.19, 0)
            tax_amount = round(total_amount - net_amount, 0)

    return {
        "source": "SII_TED_PDF417",
        "dte_code": raw_td.strip(),
        "document_type": doc_type,
        "document_number": folio or "S/N",
        "issue_date": issue_date,
        "first_item": raw_it1,
        "issuer": {
            "tax_id": issuer_rut,
            "legal_name": issuer_name or "EMISOR NO IDENTIFICADO",
            "address": "",
        },
        "receiver": {
            "tax_id": receiver_rut,
            "legal_name": receiver_name,
            "address": "",
        },
        "amounts": {
            "net_amount": net_amount,
            "tax_amount": tax_amount,
            "exempt_amount": exempt_amount,
            "total_amount": total_amount,
            "currency": "CLP",
        },
        "confidence_score": 1.0,
    }


def decode_barcodes_from_image(image_path: str) -> Optional[dict[str, Any]]:
    """
    Busca y decodifica códigos PDF417 (Timbre Electrónico SII) o códigos de barra 1D SII
    directamente desde el archivo de imagen usando zxing-cpp con múltiples pasadas
    (imagen original, recorte inferior realzado con CLAHE y reescalado).
    """
    if zxingcpp is None:
        return None

    img = cv2.imread(image_path)
    if img is None:
        return None

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bottom_half = gray[int(h * 0.35) :, :]

    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    bottom_clahe = clahe.apply(bottom_half)

    # Pasadas progresivas: primero imagen completa, luego mitad inferior con CLAHE y luego escalada 1.5x
    candidate_views: list[np.ndarray] = [
        gray,
        bottom_clahe,
    ]
    if bottom_clahe.size > 0 and max(bottom_clahe.shape[:2]) < 2200:
        upscaled = cv2.resize(
            bottom_clahe,
            None,
            fx=1.5,
            fy=1.5,
            interpolation=cv2.INTER_CUBIC,
        )
        candidate_views.append(upscaled)

    fallback_1d: Optional[dict[str, Any]] = None

    for view in candidate_views:
        try:
            barcodes = zxingcpp.read_barcodes(
                view,
                try_rotate=True,
                try_downscale=True,
            )
            for bc in barcodes:
                txt = (bc.text or "").strip()
                if not txt:
                    continue
                parsed = parse_sii_ted_xml(txt)
                if parsed:
                    if parsed.get("source") == "SII_TED_PDF417":
                        return parsed
                    if fallback_1d is None:
                        fallback_1d = parsed
        except Exception:
            continue

    return fallback_1d


def merge_sii_ted_with_ocr(
    ted_data: Optional[dict[str, Any]], ocr_data: dict[str, Any]
) -> dict[str, Any]:
    """
    Fusiona los datos criptográficos del Timbre Electrónico SII (<TED> PDF417) con los
    datos visuales del OCR:
    - El Timbre SII tiene prioridad absoluta (100% exactitud) en:
      Tipo de Documento, Folio, Fecha de Emisión, RUT Emisor, Razón Social Emisor y Monto Total.
    - El OCR visual complementa:
      Dirección de la sucursal (SUC / Dirección) y desglose exacto entre Monto Afecto y Exento
      cuando la boleta tiene ítems mixtos.
    """
    if not ted_data:
        return ocr_data

    # Si proviene solo de un código de barras 1D de 30-31 dígitos, enriquecer folio y fecha
    if ted_data.get("source") == "BARCODE_1D_SII":
        merged = dict(ocr_data)
        if ted_data.get("document_number") and ted_data["document_number"] != "S/N":
            merged["document_number"] = ted_data["document_number"]
        if ted_data.get("issue_date"):
            merged["issue_date"] = ted_data["issue_date"]
        if merged.get("document_type") == "COMPROBANTE_GENERAL":
            merged["document_type"] = "BOLETA_ELECTRONICA"
        merged["confidence_score"] = max(
            float(merged.get("confidence_score", 0.85)), 0.96
        )
        return merged

    # Fusión completa con Timbre Electrónico PDF417 (<TED>)
    ocr_issuer = ocr_data.get("issuer", {})
    ted_issuer = ted_data.get("issuer", {})

    ocr_amounts = ocr_data.get("amounts", {})
    ted_amounts = dict(ted_data.get("amounts", {}))

    ted_total = float(ted_amounts.get("total_amount", 0.0) or 0.0)
    ocr_net = float(ocr_amounts.get("net_amount", 0.0) or 0.0)
    ocr_tax = float(ocr_amounts.get("tax_amount", 0.0) or 0.0)
    ocr_exempt = float(ocr_amounts.get("exempt_amount", 0.0) or 0.0)

    # Si el OCR visual detectó un desglose con monto exento que cuadra con el Total del TED, preservar ese desglose
    if (
        ted_total > 0
        and ocr_exempt > 0
        and abs((ocr_net + ocr_tax + ocr_exempt) - ted_total) <= 5
    ):
        ted_amounts["net_amount"] = ocr_net
        ted_amounts["tax_amount"] = ocr_tax
        ted_amounts["exempt_amount"] = ocr_exempt
    elif ted_total == 0 and float(ocr_amounts.get("total_amount", 0.0) or 0.0) > 0:
        ted_amounts = ocr_amounts

    issuer_rut = ted_issuer.get("tax_id") or ocr_issuer.get("tax_id", "")
    issuer_name = ted_issuer.get("legal_name", "")
    if (
        not issuer_name
        or issuer_name == "EMISOR NO IDENTIFICADO"
    ) and ocr_issuer.get("legal_name"):
        issuer_name = ocr_issuer["legal_name"]
    if issuer_rut in CHILE_KNOWN_ISSUERS:
        issuer_name = CHILE_KNOWN_ISSUERS[issuer_rut]

    return {
        "document_type": ted_data.get("document_type") or ocr_data.get("document_type", "BOLETA_ELECTRONICA"),
        "document_number": (
            ted_data.get("document_number")
            if ted_data.get("document_number") and ted_data.get("document_number") != "S/N"
            else ocr_data.get("document_number", "S/N")
        ),
        "issue_date": ted_data.get("issue_date") or ocr_data.get("issue_date"),
        "issuer": {
            "tax_id": issuer_rut,
            "legal_name": issuer_name or "EMISOR NO IDENTIFICADO",
            "address": ocr_issuer.get("address") or ted_issuer.get("address", ""),
        },
        "receiver": {
            "tax_id": ted_data.get("receiver", {}).get("tax_id")
            or ocr_data.get("receiver", {}).get("tax_id", ""),
            "legal_name": (
                ted_data.get("receiver", {}).get("legal_name")
                if ted_data.get("receiver", {}).get("legal_name") not in ("", "CLIENTE GENERAL")
                else ocr_data.get("receiver", {}).get("legal_name", "CLIENTE GENERAL")
            ),
            "address": ocr_data.get("receiver", {}).get("address", ""),
        },
        "amounts": ted_amounts,
        "confidence_score": 1.0 if validate_rut_mod11(issuer_rut) else 0.98,
    }
