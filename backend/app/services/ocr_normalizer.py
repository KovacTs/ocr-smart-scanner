import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import cv2
import numpy as np
from PIL import Image

MODELS_DIR = Path(__file__).resolve().parent.parent / "ocr_models"
DET_V4_PATH = MODELS_DIR / "ch_PP-OCRv4_det_infer.onnx"
REC_LATIN_PATH = MODELS_DIR / "en_PP-OCRv3_rec_infer.onnx"

try:
    from rapidocr_onnxruntime import RapidOCR

    ocr_kwargs: dict[str, Any] = {
        "text_score": 0.35,
        "det_limit_side_len": 1920,
        "det_thresh": 0.25,
        "det_box_thresh": 0.30,
        "det_unclip_ratio": 1.5,
    }
    if DET_V4_PATH.exists():
        ocr_kwargs["det_model_path"] = str(DET_V4_PATH)
    if REC_LATIN_PATH.exists():
        ocr_kwargs["rec_model_path"] = str(REC_LATIN_PATH)

    _rapid_ocr_engine: Optional[RapidOCR] = RapidOCR(**ocr_kwargs)
except Exception:
    _rapid_ocr_engine = None

try:
    import pytesseract
except ImportError:
    pytesseract = None


# Patrones de ruido frecuentes en facturas y boletas térmicas que deben descartarse
NOISE_PATTERNS = [
    r"(?i).*timbre\s*electr[oó0]nic[o0]\s*sii.*",
    r"(?i).*verifique\s*documento.*",
    r"(?i).*verif[yi]\s*document.*",
    r"(?i).*res\.?\s*ex\.?\s*sii.*",
    r"(?i).*gracias\s+por\s+su\s+compra.*",
    r"(?i).*conserve\s+su\s+boleta.*",
    r"(?i).*copia\s+no\s+cedible.*",
    r"(?i).*cedible\s+con\s+su\s+factura.*",
    r"(?i).*tarjeta\s+de\s+(cr[eé]dito|d[eé]bito).*\*{2,}.*",
    r"(?i).*c[oó0]digo\s+autorizaci[oó0]n.*",
    r"(?i).*numer[o0]\s+operaci[oó0]n.*",
    r"(?i).*acepto\s+pagar\s+seg[uú]n\s+contrato.*",
    r"(?i).*punt[o0]s\s*por\s*vencer.*",
    r"(?i).*sald[o0]\s*de\s*punt[o0]s.*",
    r"(?i).*punt[o0]s\s*cencosud.*",
    r"(?i).*revisa\s+los\s+t[eé]rminos\s+y\s+condiciones.*",
    r"(?i).*fecha\s*para\s*ree?[mn]bolso.*",
    r"(?i).*card\s+no\s+tiene\s+ree?[mn]bolso.*",
    r"^[\s\-=_*#.~:]{3,}$",
]

SPANISH_MONTHS = {
    "enero": "01",
    "ene": "01",
    "febrero": "02",
    "feb": "02",
    "marzo": "03",
    "mar": "03",
    "abril": "04",
    "abr": "04",
    "mayo": "05",
    "may": "05",
    "junio": "06",
    "jun": "06",
    "julio": "07",
    "jul": "07",
    "agosto": "08",
    "ago": "08",
    "septiembre": "09",
    "sep": "09",
    "set": "09",
    "octubre": "10",
    "oct": "10",
    "noviembre": "11",
    "nov": "11",
    "diciembre": "12",
    "dic": "12",
}


def enhance_document_for_ocr(img: np.ndarray) -> np.ndarray:
    """
    Preprocesamiento de imagen especializado para facturas y boletas térmicas:
    1. Normalización de contraste local en canal L (espacio LAB) con CLAHE para rescatar tinta tenue
       y atenuar sombras proyectadas por el teléfono.
    2. Máscara de enfoque (Unsharp Masking) para definir los trazos de letras y números pequeños.
    """
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)

    clahe = cv2.createCLAHE(clipLimit=2.4, tileGridSize=(8, 8))
    l_enhanced = clahe.apply(l_chan)

    merged = cv2.merge((l_enhanced, a_chan, b_chan))
    enhanced_bgr = cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)

    # Realce de bordes de caracteres (Unsharp Mask)
    blurred = cv2.GaussianBlur(enhanced_bgr, (0, 0), 1.4)
    sharpened = cv2.addWeighted(enhanced_bgr, 1.55, blurred, -0.55, 0)
    return sharpened


def _boxes_overlap_horizontally(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """
    Retorna True si dos cajas de texto se solapan horizontalmente (están en la misma columna X),
    lo que indica que pertenecen a renglones distintos aunque el papel esté curvado.
    """
    overlap = min(a["max_x"], b["max_x"]) - max(a["x"], b["x"])
    min_w = max(min(a["max_x"] - a["x"], b["max_x"] - b["x"]), 1.0)
    return (overlap / min_w) > 0.25


def _reconstruct_spatial_lines(ocr_res: list[Any]) -> str:
    """
    Agrupa las cajas de texto detectadas según su coordenada vertical (Y) y no-solapamiento
    horizontal (X), ordenándolas de izquierda a derecha para reconstruir las filas de boletas
    incluso cuando el papel térmico presenta curvatura.
    """
    if not ocr_res:
        return ""
    items = []
    for box, txt, score in ocr_res:
        if float(score) < 0.40:
            continue
        ys = [pt[1] for pt in box]
        xs = [pt[0] for pt in box]
        cy = sum(ys) / 4.0
        h = max(ys) - min(ys)
        min_x = min(xs)
        max_x = max(xs)
        items.append(
            {
                "cy": cy,
                "h": max(h, 10.0),
                "x": min_x,
                "max_x": max_x,
                "txt": str(txt).strip(),
            }
        )

    items.sort(key=lambda it: it["cy"])
    lines: list[list[dict[str, Any]]] = []
    for it in items:
        placed = False
        for line in reversed(lines[-3:]):
            avg_cy = sum(x["cy"] for x in line) / len(line)
            avg_h = sum(x["h"] for x in line) / len(line)
            if abs(it["cy"] - avg_cy) <= avg_h * 0.52 and not any(
                _boxes_overlap_horizontally(it, existing) for existing in line
            ):
                line.append(it)
                placed = True
                break
        if not placed:
            lines.append([it])

    lines.sort(key=lambda ln: sum(x["cy"] for x in ln) / len(ln))
    out_lines = []
    for line in lines:
        line.sort(key=lambda x: x["x"])
        out_lines.append("  ".join(x["txt"] for x in line))
    return "\n".join(out_lines)


def extract_raw_text_from_image(
    image_path: str, raw_text_hint: Optional[str] = None
) -> str:
    """
    Extrae el texto real de la imagen usando:
    1. Texto provisto opcionalmente (hint/tests).
    2. Preprocesamiento CLAHE + Unsharp Mask + RapidOCR (Detector PP-OCRv4 + Reconocedor Latino).
    3. Pase Foveado de Alta Resolución en Encabezado (Multi-Scale Header Zoom) cuando el encabezado
       presenta letra pequeña o curvatura y no se detectó la línea de Folio/Boleta en la vista general.
    4. Fallback con Tesseract si está instalado.
    """
    if raw_text_hint and raw_text_hint.strip():
        return raw_text_hint.strip()

    if _rapid_ocr_engine is not None:
        try:
            img = cv2.imread(image_path)
            if img is not None:
                enhanced_img = enhance_document_for_ocr(img)
                ocr_result, _ = _rapid_ocr_engine(enhanced_img)
                if ocr_result:
                    reconstructed = _reconstruct_spatial_lines(ocr_result)
                    # Si el pase completo no capturó el número de boleta/factura en el encabezado por curvatura o distancia,
                    # hacer un zoom dedicado sobre el tercio superior central del documento
                    if not re.search(
                        r"(?i)(?:[b38][o0]l(?:eta|\.)?\s*electr|factura\s*electr|f[o0]li[o0])\D{0,12}\d{4,14}",
                        reconstructed,
                    ):
                        h, w = enhanced_img.shape[:2]
                        header_crop = enhanced_img[
                            int(h * 0.05) : int(h * 0.42),
                            int(w * 0.14) : int(w * 0.86),
                        ]
                        if header_crop.size > 0:
                            header_res, _ = _rapid_ocr_engine(header_crop)
                            if header_res:
                                header_text = _reconstruct_spatial_lines(header_res)
                                if header_text.strip():
                                    reconstructed = (
                                        header_text.strip() + "\n" + reconstructed
                                    )
                    if reconstructed.strip():
                        return reconstructed.strip()
        except Exception:
            pass

    if pytesseract is not None and shutil.which("tesseract"):
        try:
            img = cv2.imread(image_path)
            if img is not None:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                processed = cv2.adaptiveThreshold(
                    gray,
                    255,
                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY,
                    31,
                    12,
                )
                pil_img = Image.fromarray(processed)
                text = pytesseract.image_to_string(pil_img, lang="spa+eng")
                if text and text.strip():
                    return text.strip()
        except Exception:
            pass

    return ""


def normalize_ocr_confusions_in_keywords(line: str) -> str:
    """
    Corrige confusiones típicas de OCR donde el número '0' reemplaza a la letra 'O'
    dentro de palabras en mayúsculas (ej. 'T0TAL' -> 'TOTAL', 'NET0' -> 'NETO', 'M0NT0' -> 'MONTO').
    """
    return re.sub(
        r"(?<=[A-ZÁÉÍÓÚÑ])0(?=[A-ZÁÉÍÓÚÑ]|$)|(?<=^)[TNM]0(?=[A-ZÁÉÍÓÚÑ])",
        lambda m: m.group(0).replace("0", "O"),
        line.upper(),
    )


def clean_ocr_noise(raw_text: str) -> list[str]:
    """
    Filtra líneas irrelevantes (timbres legales, publicidad, puntos de fidelidad, POS).
    """
    cleaned_lines: list[str] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        is_noise = False
        for pattern in NOISE_PATTERNS:
            if re.match(pattern, stripped):
                is_noise = True
                break
        if not is_noise:
            cleaned_lines.append(stripped)
    return cleaned_lines


def format_rut(raw_rut: str) -> str:
    """
    Normaliza un RUT chileno o identificador fiscal al formato estándar XX.XXX.XXX-X.
    """
    clean = re.sub(r"[^0-9kK]", "", raw_rut).upper()
    if len(clean) < 7:
        return raw_rut.strip()
    body, dv = clean[:-1], clean[-1]
    reversed_digits = body[::-1]
    groups = [
        reversed_digits[i : i + 3][::-1]
        for i in range(0, len(reversed_digits), 3)
    ]
    formatted_body = ".".join(reversed(groups))
    return f"{formatted_body}-{dv}"


def validate_rut_mod11(rut: str) -> bool:
    """
    Valida el dígito verificador en módulo 11 para asegurar certeza del OCR.
    """
    clean = re.sub(r"[^0-9kK]", "", rut).upper()
    if len(clean) < 2:
        return False
    body, dv = clean[:-1], clean[-1]
    if not body.isdigit():
        return False
    factors = [2, 3, 4, 5, 6, 7]
    total = sum(
        int(digit) * factors[i % len(factors)]
        for i, digit in enumerate(reversed(body))
    )
    remainder = 11 - (total % 11)
    expected_dv = (
        "0" if remainder == 11 else ("K" if remainder == 10 else str(remainder))
    )
    return dv == expected_dv


def parse_iso_date(text: str) -> str:
    """
    Detecta fechas en formato DD/MM/YYYY, DD/MM/YY, YYYY-MM-DD o '15 de Octubre de 2026'
    y las convierte a formato ISO 8601 (YYYY-MM-DD).
    """
    m_iso = re.search(
        r"(20\d{2})[-/.](0[1-9]|1[0-2])[-/.](0[1-9]|[12]\d|3[01])", text
    )
    if m_iso:
        return f"{m_iso.group(1)}-{m_iso.group(2)}-{m_iso.group(3)}"

    m_dmy4 = re.search(
        r"(0[1-9]|[12]\d|3[01])[/-](0[1-9]|1[0-2])[/-](20\d{2})", text
    )
    if m_dmy4:
        return f"{m_dmy4.group(3)}-{m_dmy4.group(2)}-{m_dmy4.group(1)}"

    m_dmy2 = re.search(
        r"(0[1-9]|[12]\d|3[01])[/-](0[1-9]|1[0-2])[/-](2\d)", text
    )
    if m_dmy2:
        year = 2000 + int(m_dmy2.group(3))
        return f"{year:04d}-{m_dmy2.group(2)}-{m_dmy2.group(1)}"

    m_text = re.search(
        r"(?i)\b(0?[1-9]|[12]\d|3[01])(?:\s+de\s+|[\s\-/])([a-z]{3,10})(?:\s+del?\s+|[\s\-/])(20\d{2}|\d{2})\b",
        text,
    )
    if m_text:
        day = int(m_text.group(1))
        month_name = m_text.group(2).lower()
        month_num = SPANISH_MONTHS.get(month_name)
        if month_num:
            year_str = m_text.group(3)
            year = int(year_str) if len(year_str) == 4 else 2000 + int(year_str)
            return f"{year:04d}-{month_num}-{day:02d}"

    return datetime.now().strftime("%Y-%m-%d")


def parse_monetary_value(raw_val: str) -> float:
    """
    Normaliza montos monetarios eliminando '$', 'CLP', 'USD', separadores de miles '.'
    y convirtiendo decimales a float puro.
    """
    cleaned = re.sub(r"[^\d.,]", "", raw_val).strip()
    if not cleaned:
        return 0.0

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "." in cleaned:
        parts = cleaned.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            cleaned = "".join(parts)
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            cleaned = "".join(parts)
        else:
            cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# Directorio de comercios y emisores frecuentes en Chile (enriquecimiento por RUT validado)
CHILE_KNOWN_ISSUERS: dict[str, str] = {
    "76.134.941-4": "HIPER LIMITADA (LIDER)",
    "81.201.000-K": "CENCOSUD RETAIL S.A.",
    "76.507.443-6": "DECATHLON CHILE SPA",
    "96.509.660-4": "FALABELLA RETAIL S.A.",
    "96.792.430-K": "SODIMAC S.A.",
    "78.627.210-6": "HIPERMARCADOS TOTTUS S.A.",
    "76.007.445-3": "RENDIC HERMANOS S.A. (UNIMARC)",
    "99.520.000-7": "COMPANIA DE PETROLEOS DE CHILE COPEC S.A.",
    "89.807.200-2": "FARMACIAS CRUZ VERDE S.A.",
    "76.031.071-8": "SALCOBRAND S.A.",
}

# Palabras de procesadores de pago en pie de boleta que nunca deben tomarse como el comercio emisor
PAYMENT_PROCESSOR_KEYWORDS = (
    "PLUXEE",
    "PIUXEE",
    "JUNAEB",
    "SODEXO",
    "EDENRED",
    "TRANSBANK",
    "REDCOMPRA",
    "GETNET",
    "SERVICIO ALIMENTACION",
)


def classify_document_type(text: str) -> str:
    upper = normalize_ocr_confusions_in_keywords(text)
    if "FACTURA EXENTA" in upper or "NO AFECTA O EXENTA" in upper:
        return "FACTURA_EXENTA"
    if "BOLETA DE HONORARIOS" in upper or "HONORARIOS" in upper:
        return "BOLETA_HONORARIOS"
    if "FACTURA" in upper:
        return "FACTURA_ELECTRONICA"
    if (
        "BOLETA" in upper
        or re.search(r"\b[B38][O0]L\.?\s*ELECTR", upper)
        or re.search(r"\bB[O0]LE[TL]A\b", upper)
        or ("ELECTRONICA" in upper and ("CAJA" in upper or "VUELTO" in upper))
    ):
        return "BOLETA_ELECTRONICA"
    return "COMPROBANTE_GENERAL"


def _clean_candidate_issuer_line(line: str) -> str:
    """
    Limpia una línea de encabezado quitando fragmentos de RUT y sucursal en la misma línea
    (ej. 'HIPER LIMITADA - RUT: 76.134.941-4' -> 'HIPER LIMITADA').
    """
    cleaned = re.split(
        r"(?i)\b(?:R\.?U\.?T\.?|SUC\s*:|GIRO\s*:|BOLETA|FACTURA|[B38]OL\.\s*ELECTR)",
        line,
    )[0]
    cleaned = re.sub(
        r"(?i)^(?:raz[oó0]n\s+social|emisor|comercio)\s*:\s*", "", cleaned
    )
    cleaned = cleaned.strip(" -–—:.,|")
    return cleaned


def normalize_document_text(raw_text: str) -> dict[str, Any]:
    """
    Ejecuta el pipeline completo de normalización sobre el texto extraído por el OCR:
    1. Descarta ruido de impresión térmica y leyendas legales.
    2. Clasifica tipo de documento (Factura, Boleta Electrónica, etc.) con tolerancia a errores ópticos.
    3. Extrae Folio (incluyendo abreviaciones 'Bol. Electronica' y respaldo de timbre SII de 31 dígitos).
    4. Extrae RUT Emisor/Receptor validado en Módulo 11, Razón Social (con separación en la misma línea
       y enriquecimiento de comercios chilenos) y Montos (incluyendo 'TOTAL AFECTO' / 'EXENTO').
    """
    lines = clean_ocr_noise(raw_text)
    clean_text = "\n".join(lines)

    doc_type = classify_document_type(clean_text)

    # 1. Extraer Folio / Número de documento
    # Soporta:
    # - 'BOLETA ELECTRONICA N 3312694425', 'BOLETA ELECTRONICA N_0312694425'
    # - 'Bol. Electronica: 002640601308', '3ol. Electronical 002640601308'
    # - 'BOLETA ELECTRONICA:19520723', 'Boleta: 91088449'
    # - Respaldo de código numérico de timbre SII (31 dígitos: YYYYMMDD + local/caja/trx + 10 dígitos folio + DV)
    folio = ""
    folio_patterns = [
        r"(?i)(?:f[o0]li[o0]|factura\s*electr[oó0]nica|[b38][o0]l(?:eta|\.)?\s*electr[oó0]nica[l1:]?)\s*(?:n[°ºo09c&*._\-]{0,3}\s+|nro\.?\s*)?[:#\-_]*\s*(\d{3,13}[\daBbOoIlSs])",
        r"(?i)(?:f[o0]li[o0]|factura\s*electr[oó0]nica|[b38][o0]l(?:eta|\.)?\s*electr[oó0]nica[l1:]?)\s*(?:n[°ºo09c&*._\-]{0,3})?[:#\-_]+\s*(\d{3,13}[\daBbOoIlSs])",
        r"(?i)\b(?:f[o0]li[o0]|b[o0]le[tl]a|factura)\s*(?:n[°ºo09c&*._\-]{0,3}\s*|nro\.?\s*)?[:#\-_]+\s*(\d{3,13}[\daBbOoIlSs])",
        r"(?i)\b(?:f[o0]li[o0]|b[o0]le[tl]a|factura)\s+(?:n[°ºo09c&*._\-]+\s*|nro\.?\s+)(\d{3,13}[\daBbOoIlSs])",
        r"(?i)\bN[°º&*]\s*(\d{4,14})\b",
    ]
    digit_fix_map = str.maketrans({"a": "8", "A": "8", "b": "8", "B": "8", "o": "0", "O": "0", "I": "1", "l": "1", "S": "5", "s": "5"})
    for pat in folio_patterns:
        m = re.search(pat, clean_text)
        if m:
            folio = m.group(1).translate(digit_fix_map)
            break

    # Respaldo: extraer o corregir folio desde la línea de timbre numérico SII (30-31 dígitos: YYYYMMDD + local/caja/trx + 10 dígitos folio + DV)
    m_sii_stamp = re.search(r"\b20\d{6}\d{11,12}(\d{10})\d\b", clean_text)
    if not folio and m_sii_stamp:
        folio = m_sii_stamp.group(1)
    elif folio and len(folio) == 10 and m_sii_stamp:
        if m_sii_stamp.group(1)[1:] == folio[1:]:
            folio = m_sii_stamp.group(1)

    # 2. Extraer Fecha de emisión en formato ISO YYYY-MM-DD
    issue_date = parse_iso_date(clean_text)

    # 3. Extraer RUTs validados en Módulo 11 (ignorando RUTs de procesadores de pago como Pluxee/JUNAEB para el emisor)
    normalized_ruts: list[str] = []
    footer_payment_ruts: set[str] = set()

    for line in lines:
        upper_l = line.upper()
        line_ruts = re.findall(
            r"(?i)R\.?U\.?T\.?\s*(?:RECEPTOR|EMISOR)?\s*[:.]?\s*(\d{1,2}\.?\d{3}\.?\d{3}-?[\dkK]|\d{7,8}-?[\dkK])",
            line,
        ) + re.findall(
            r"\b(?:\d{1,2}\.?\d{3}\.?\d{3}|\d{7,8})-[\dkK]\b", line
        )
        for cand in line_ruts:
            fmt = format_rut(cand)
            if any(pk in upper_l for pk in PAYMENT_PROCESSOR_KEYWORDS):
                footer_payment_ruts.add(fmt)
            if fmt not in normalized_ruts and validate_rut_mod11(fmt):
                normalized_ruts.append(fmt)

    if not normalized_ruts:
        all_cands = re.findall(
            r"(?i)R\.?U\.?T\.?\s*(?:RECEPTOR|EMISOR)?\s*[:.]?\s*(\d{1,2}\.?\d{3}\.?\d{3}-?[\dkK]|\d{7,8}-?[\dkK])",
            clean_text,
        ) + re.findall(
            r"\b(?:\d{1,2}\.?\d{3}\.?\d{3}|\d{7,8})-[\dkK]\b", clean_text
        )
        for cand in all_cands:
            fmt = format_rut(cand)
            if fmt not in normalized_ruts:
                normalized_ruts.append(fmt)

    non_payment_ruts = [r for r in normalized_ruts if r not in footer_payment_ruts]
    primary_ruts = non_payment_ruts if non_payment_ruts else normalized_ruts

    issuer_rut = primary_ruts[0] if len(primary_ruts) >= 1 else ""
    receiver_rut = primary_ruts[1] if len(primary_ruts) >= 2 else ""

    # 4. Extraer Razón Social (Comercio) y Dirección del Emisor y Receptor
    issuer_name = ""
    issuer_address = ""
    receiver_name = ""

    corporate_suffixes = (
        "SPA",
        "LIMITADA",
        "LTDA",
        "S.A",
        "E.I.R.L",
        "EIRL",
        "COMERCIAL",
        "RETAIL",
        "SERVICIOS",
        "SUPERMERCADO",
        "HIPER",
        "DECATHLON",
        "CENCOSUD",
        "FALABELLA",
        "SODIMAC",
        "TOTTUS",
        "UNIMARC",
        "COPEC",
    )

    for idx, line in enumerate(lines):
        upper_l = normalize_ocr_confusions_in_keywords(line)

        # Ignorar líneas de procesadores de pago del pie de página (Pluxee, JUNAEB, Transbank)
        if any(pk in upper_l for pk in PAYMENT_PROCESSOR_KEYWORDS):
            continue

        if not receiver_name and any(
            k in upper_l
            for k in ("SEÑOR(ES)", "SENOR(ES)", "CLIENTE:", "RECEPTOR:", "NOMBRE:")
        ):
            parts = re.split(
                r"(?i)(?:señor\(es\)|senor\(es\)|cliente|receptor|nombre)\s*:\s*",
                line,
            )
            if len(parts) > 1 and parts[1].strip():
                cand_rec = parts[1].strip()
                if cand_rec.upper() != "CLIENTE":
                    receiver_name = cand_rec
            continue

        if not issuer_address and any(
            k in upper_l
            for k in (
                "DIRECCION",
                "DIRECCIÓN",
                "SUC:",
                "SUC.:",
                "AV ",
                "AV.",
                "AVENIDA",
                "CALLE ",
                "PASEO ",
            )
        ):
            addr_line = line
            if "SUC:" in upper_l or "SUC.:" in upper_l:
                addr_parts = re.split(r"(?i)SUC\.?\s*:\s*", line)
                if len(addr_parts) > 1:
                    addr_line = re.split(r"(?i)\bR\.?U\.?T", addr_parts[1])[0]
            else:
                addr_line = re.sub(
                    r"(?i)^direcci[oó0]n\s*:\s*", "", addr_line
                )
            issuer_address = addr_line.strip(" -–—:.,")

        # Buscar el comercio emisor en el encabezado (primeras 10 líneas) incluso si comparte línea con el RUT
        if not issuer_name and idx < 10:
            candidate = _clean_candidate_issuer_line(line)
            cand_upper = normalize_ocr_confusions_in_keywords(candidate)
            if (
                len(candidate) >= 3
                and any(suffix in cand_upper for suffix in corporate_suffixes)
                and not any(
                    k in cand_upper
                    for k in ("SEÑOR", "CLIENTE", "GIRO", "PUNTOS", "SII SANTIAGO")
                )
            ):
                issuer_name = candidate

    # Enriquecimiento con directorio de comercios conocidos por RUT o marca
    if issuer_rut in CHILE_KNOWN_ISSUERS:
        issuer_name = CHILE_KNOWN_ISSUERS[issuer_rut]

    if not issuer_name and lines:
        for line in lines[:5]:
            cand = _clean_candidate_issuer_line(line)
            if (
                len(cand) >= 4
                and not re.search(
                    r"(?i)(rut|factura|boleta|[b38]ol\.|folio|fecha|sii\s+santiago|sii\s+saniago|suc:|av\.|avenida)",
                    cand,
                )
            ):
                issuer_name = cand
                break

    # 5. Extraer Montos Monetarios (soporta 'TOTAL AFECTO', 'EXENTO', 'NETO', 'IVA', 'TOTAL')
    net_amount = 0.0
    tax_amount = 0.0
    exempt_amount = 0.0
    total_amount = 0.0
    currency = "USD" if "USD" in clean_text.upper() else "CLP"

    for line in lines:
        upper_l = normalize_ocr_confusions_in_keywords(line)
        line_without_pct = re.sub(r"\d+(?:[.,]\d+)?\s*%", "", upper_l)

        # Buscar pares explícitos ETIQUETA + MONTO dentro de la misma línea
        pair_matches = re.findall(
            r"(SUB\s*TOTAL|TOTAL\s*AFECTO|MONTO\s*AFECTO|NETO\s*TOTAL|MONTO\s*NETO|NETO|TOTAL\s*IVA|I\.?V\.?A\.?|IMPUESTO|TOTAL\s*EXENTO|MONTO\s*EXENTO|EXENTO|MONTO\s*TOTAL|TOTAL(?!\s*NUMERO|\s*ARTIC))[^$\d]{0,18}\$?\s*(\d{1,3}(?:[.,\s]\d{3})+|\d{3,9})",
            line_without_pct,
        )
        if pair_matches:
            for label, raw_num in pair_matches:
                val = parse_monetary_value(raw_num.replace(" ", ""))
                if val <= 0:
                    continue
                if "IVA" in label or "IMPUESTO" in label:
                    if tax_amount == 0.0:
                        tax_amount = val
                elif "NETO" in label or "AFECTO" in label:
                    if net_amount == 0.0:
                        net_amount = val
                elif "EXENTO" in label:
                    if exempt_amount == 0.0:
                        exempt_amount = val
                elif "SUB" in label:
                    if total_amount == 0.0:
                        total_amount = val
                elif "TOTAL" in label:
                    total_amount = max(total_amount, val)
            continue

        # Fallback por línea cuando la columna de número está separada
        num_matches = re.findall(
            r"\$?\s*\d{1,3}(?:[.,\s]\d{3})+|\$?\s*\d{3,9}", line_without_pct
        )
        if not num_matches:
            continue
        val = parse_monetary_value(num_matches[-1].replace(" ", ""))
        if val <= 0:
            continue

        if any(k in upper_l for k in ("I.V.A", "IVA", "IMPUESTO")):
            if tax_amount == 0.0:
                tax_amount = val
        elif ("NETO" in upper_l or "AFECTO" in upper_l) and net_amount == 0.0:
            net_amount = val
        elif "EXENTO" in upper_l and exempt_amount == 0.0:
            exempt_amount = val
        elif (
            "TOTAL" in upper_l
            and not any(
                k in upper_l
                for k in ("SUB", "NETO", "AFECTO", "EXENTO", "NUMERO", "ARTIC")
            )
        ):
            total_amount = max(total_amount, val)
        elif (
            any(k in upper_l for k in ("SUB TOTAL", "SUBTOTAL", "MONTO"))
            and total_amount == 0.0
        ):
            total_amount = val

    # Reconciliación contable chilena (Neto Afecto + IVA 19% + Exento = Total)
    if total_amount > 0:
        # Si por curvatura del papel el IVA (19% del Afecto) quedó alineado con la fila 'TOTAL EXENTO'
        if (
            net_amount > 0
            and exempt_amount > 0
            and abs(round(net_amount * 0.19, 0) - exempt_amount) <= 5
            and abs((net_amount + exempt_amount) - total_amount) <= 5
        ):
            tax_amount = exempt_amount
            exempt_amount = 0.0

        if net_amount > 0 and tax_amount > 0 and exempt_amount == 0:
            residual = round(total_amount - (net_amount + tax_amount), 0)
            if residual > 10 and abs(round(net_amount * 0.19, 0) - tax_amount) <= 5:
                exempt_amount = residual
        expected_net = round((total_amount - exempt_amount) / 1.19, 0)
        if (
            net_amount == 0
            or abs((net_amount + tax_amount + exempt_amount) - total_amount) > 5
        ):
            if doc_type != "FACTURA_EXENTA":
                net_amount = expected_net
                tax_amount = round(total_amount - exempt_amount - net_amount, 0)
            else:
                exempt_amount = total_amount
    elif net_amount > 0 and total_amount == 0:
        if tax_amount == 0 and doc_type != "FACTURA_EXENTA":
            tax_amount = round(net_amount * 0.19, 0)
        total_amount = net_amount + tax_amount + exempt_amount

    checks = [
        bool(folio),
        bool(issue_date),
        bool(issuer_rut and validate_rut_mod11(issuer_rut)),
        bool(issuer_name),
        total_amount > 0,
    ]
    confidence_score = round(0.55 + (sum(checks) / len(checks)) * 0.43, 2)

    return {
        "document_type": doc_type,
        "document_number": folio or "S/N",
        "issue_date": issue_date,
        "issuer": {
            "tax_id": issuer_rut,
            "legal_name": issuer_name or "EMISOR NO IDENTIFICADO",
            "address": issuer_address,
        },
        "receiver": {
            "tax_id": receiver_rut,
            "legal_name": receiver_name or "CLIENTE GENERAL",
            "address": "",
        },
        "amounts": {
            "net_amount": net_amount,
            "tax_amount": tax_amount,
            "exempt_amount": exempt_amount,
            "total_amount": total_amount,
            "currency": currency,
        },
        "confidence_score": confidence_score,
    }
