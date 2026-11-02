import io
import cv2
import numpy as np
from openpyxl import load_workbook
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def generate_synthetic_document_image(blurry: bool = False, high_res: bool = False) -> bytes:
    """
    Genera una imagen sintética de una factura con bordes definidos y texto simulado.
    Si blurry=True, aplica desenfoque Gaussiano fuerte para simular cámara fuera de foco.
    """
    width, height = (3000, 4000) if high_res else (800, 1100)
    img = np.full((height, width, 3), 40, dtype=np.uint8)  # Fondo oscuro (mesa)

    # Papel blanco del documento dentro del encuadre (ROI)
    pad_x, pad_y = int(width * 0.1), int(height * 0.1)
    cv2.rectangle(
        img,
        (pad_x, pad_y),
        (width - pad_x, height - pad_y),
        (250, 250, 250),
        -1,
    )
    cv2.rectangle(
        img,
        (pad_x + 15, pad_y + 15),
        (width - pad_x - 15, height - pad_y - 15),
        (10, 10, 10),
        4,
    )

    # Simular líneas de texto con alto contraste y bordes nítidos
    lines = [
        "FACTURA ELECTRONICA N° 20589",
        "RUT: 76.543.210-K",
        "COMERCIAL TECNOLOGICA SUR SPA",
        "FECHA: 01/11/2026",
        "NETO: $ 200.000  IVA: $ 38.000",
        "TOTAL: $ 238.000",
    ]
    y_pos = pad_y + 100
    font_scale = 2.2 if high_res else 0.85
    thickness = 4 if high_res else 2
    for text_line in lines:
        cv2.putText(
            img,
            text_line,
            (pad_x + 50, y_pos),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 0, 0),
            thickness,
        )
        y_pos += int(90 * (font_scale / 0.85))

    # Cuadrícula de tabla de ítems para bordes realistas de documento
    for r in range(6):
        ry = y_pos + r * int(45 * (font_scale / 0.85))
        cv2.line(
            img,
            (pad_x + 40, ry),
            (width - pad_x - 40, ry),
            (20, 20, 20),
            thickness,
        )

    if blurry:
        img = cv2.GaussianBlur(img, (51, 51), 18)

    _, encoded = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 96])
    return encoded.tobytes()


def test_full_smart_ocr_pipeline():
    # 1. Registro y Login de Usuario
    reg_res = client.post(
        "/api/auth/register",
        json={
            "email": "auditor@empresa.cl",
            "password": "PasswordSeguro123!",
            "full_name": "Auditor Contable",
        },
    )
    if reg_res.status_code == 400:
        reg_res = client.post(
            "/api/auth/login",
            json={"email": "auditor@empresa.cl", "password": "PasswordSeguro123!"},
        )
    assert reg_res.status_code in (200, 201)
    token = reg_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Evaluación de Enfoque: Imagen Borrosa -> Debe ser rechazada (YELLOW/RED, is_focused=False)
    blurry_bytes = generate_synthetic_document_image(blurry=True)
    focus_blur_res = client.post(
        "/api/scans/evaluate-focus",
        headers=headers,
        files={"file": ("blurry.jpg", blurry_bytes, "image/jpeg")},
    )
    assert focus_blur_res.status_code == 200
    blur_data = focus_blur_res.json()
    assert blur_data["is_focused"] is False
    assert blur_data["status_color"] in ("YELLOW", "RED")

    # Intentar subir la foto borrosa sin force_capture debe devolver 422 IMAGE_OUT_OF_FOCUS
    reject_res = client.post(
        "/api/scans",
        headers=headers,
        files={"file": ("blurry.jpg", blurry_bytes, "image/jpeg")},
    )
    assert reject_res.status_code == 422
    assert reject_res.json()["detail"]["error"] == "IMAGE_OUT_OF_FOCUS"

    # 3. Evaluación de Enfoque: Imagen Nítida en Alta Resolución -> GREEN, is_focused=True
    sharp_bytes = generate_synthetic_document_image(blurry=False, high_res=True)
    focus_sharp_res = client.post(
        "/api/scans/evaluate-focus",
        headers=headers,
        files={"file": ("sharp.jpg", sharp_bytes, "image/jpeg")},
    )
    assert focus_sharp_res.status_code == 200
    sharp_data = focus_sharp_res.json()
    assert sharp_data["is_focused"] is True
    assert sharp_data["status_color"] == "GREEN"
    assert sharp_data["blur_score"] >= sharp_data["threshold"]

    # 4. Subida, Compresión WebP, Limpieza de Ruido y Normalización de Factura
    noisy_invoice_text = (
        "SERVICIOS INDUSTRIALES DEL SUR SPA\n"
        "RUT: 76543210-K\n"
        "DIRECCION: AV. LOS LEONES 950, PROVIDENCIA\n"
        "FACTURA ELECTRONICA\n"
        "FOLIO N° 50891\n"
        "FECHA: 15 de Octubre de 2026\n"
        "SEÑOR(ES): CONSTRUCTORA ANDES LTDA\n"
        "RUT: 77.888.999-1\n"
        "--------------------------------------\n"
        "MONTO NETO: $ 500.000\n"
        "IVA 19%: $ 95.000\n"
        "TOTAL: $ 595.000\n"
        "--------------------------------------\n"
        "TIMBRE ELECTRONICO SII RES. EX. 80\n"
        "COPIA NO CEDIBLE - GRACIAS POR SU COMPRA\n"
        "TARJETA DE CREDITO **** 4512"
    )

    scan_res = client.post(
        "/api/scans",
        headers=headers,
        files={"file": ("invoice_hd.jpg", sharp_bytes, "image/jpeg")},
        data={"raw_text_hint": noisy_invoice_text},
    )
    assert scan_res.status_code == 201
    scan_json = scan_res.json()
    scan_id = scan_json["id"]

    # Verificar reducción WebP (debe ser significativamente menor al JPEG HD original)
    assert scan_json["file_size_bytes"] < scan_json["original_size_bytes"]
    assert scan_json["image_url"].endswith("document.webp")
    assert scan_json["thumbnail_url"].endswith("thumbnail.webp")

    # Verificar Normalización de Datos y Limpieza de Ruido
    norm = scan_json["current_normalized_data"]
    assert norm["document_type"] == "FACTURA_ELECTRONICA"
    assert norm["document_number"] == "50891"
    assert norm["issue_date"] == "2026-10-15"  # Convertido a ISO 8601
    assert norm["issuer"]["tax_id"] == "76.543.210-K"  # Formateado estándar
    assert norm["issuer"]["legal_name"] == "SERVICIOS INDUSTRIALES DEL SUR SPA"
    assert norm["receiver"]["tax_id"] == "77.888.999-1"
    assert norm["amounts"]["net_amount"] == 500000.0
    assert norm["amounts"]["tax_amount"] == 95000.0
    assert norm["amounts"]["total_amount"] == 595000.0
    assert len(scan_json["versions"]) == 1
    assert scan_json["versions"][0]["version_number"] == 1

    # 5. Actualización Manual y Control de Versiones (Versión #2)
    updated_norm = dict(norm)
    updated_norm["document_number"] = "50891-CORREGIDO"
    upd_res = client.put(
        f"/api/scans/{scan_id}",
        headers=headers,
        json={"normalized_data": updated_norm},
    )
    assert upd_res.status_code == 200
    upd_json = upd_res.json()
    assert upd_json["folio"] == "50891-CORREGIDO"
    assert len(upd_json["versions"]) == 2
    assert upd_json["versions"][0]["version_number"] == 2
    assert upd_json["versions"][0]["modified_by"] == "auditor@empresa.cl"

    # 6. Descarga de Imagen WebP protegida
    img_res = client.get(scan_json["image_url"], headers=headers)
    assert img_res.status_code == 200
    assert img_res.headers["content-type"] == "image/webp"

    # 7. Exportación a Excel (.xlsx) Individual y por Lotes
    excel_res = client.post(
        "/api/scans/export-batch",
        headers=headers,
        json={"scan_ids": [scan_id]},
    )
    assert excel_res.status_code == 200
    assert (
        excel_res.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    wb = load_workbook(io.BytesIO(excel_res.content))
    ws = wb.active
    assert ws.title == "Reporte Escaneos OCR"
    assert ws.cell(row=2, column=1).value == "50891-CORREGIDO"
    assert ws.cell(row=2, column=4).value == "76.543.210-K"
    assert ws.cell(row=2, column=10).value == 595000.0
    assert str(ws.cell(row=3, column=10).value).startswith("=SUM(")


def test_sii_ted_pdf417_xml_extraction_and_fusion():
    """
    Valida que al decodificar un Timbre Electrónico SII (<TED> PDF417) en una Boleta Electrónica (TD=39)
    o Factura (TD=33), el sistema extraiga con 100% de exactitud el Folio, RUT Emisor, Razón Social,
    Fecha ISO y Montos, fusionándose con la dirección detectada por el OCR visual.
    """
    login_res = client.post(
        "/api/auth/login",
        json={"email": "auditor@empresa.cl", "password": "PasswordSeguro123!"},
    )
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    sample_ted_xml = """
    <TED version="1.0">
      <DD>
        <RE>76134941-4</RE>
        <TD>39</TD>
        <F>002640601308</F>
        <FE>2026-11-02</FE>
        <RR>66666666-6</RR>
        <RSR>BOLETA</RSR>
        <MNT>42241</MNT>
        <IT1>LECHE ENTERA</IT1>
        <CAF version="1.0">
          <DA>
            <RE>76134941-4</RE>
            <RS>HIPER LIMITADA</RS>
            <TD>39</TD>
          </DA>
        </CAF>
      </DD>
    </TED>
    """

    partial_ocr_text = (
        "SUC: AV. Vicuna Mackenna 3361\n"
        "TOTAL AFECTO $ 35.497\n"
        "TOTAL IVA (19.0%) $ 6.744\n"
        "TOTAL $ 42.241"
    )

    sharp_bytes = generate_synthetic_document_image(blurry=False, high_res=False)
    scan_res = client.post(
        "/api/scans",
        headers=headers,
        files={"file": ("boleta_lider.jpg", sharp_bytes, "image/jpeg")},
        data={
            "raw_text_hint": partial_ocr_text,
            "sii_ted_xml": sample_ted_xml,
        },
    )
    assert scan_res.status_code == 201
    scan_json = scan_res.json()

    assert scan_json["document_type"] == "BOLETA_ELECTRONICA"
    assert scan_json["folio"] == "002640601308"
    assert scan_json["issue_date"] == "2026-11-02"
    assert scan_json["total_amount"] == 42241.0

    norm = scan_json["current_normalized_data"]
    assert norm["issuer"]["tax_id"] == "76.134.941-4"
    assert norm["issuer"]["legal_name"] == "HIPER LIMITADA (LIDER)"
    assert "Vicuna Mackenna 3361" in norm["issuer"]["address"]
    assert norm["amounts"]["net_amount"] == 35497.0
    assert norm["amounts"]["tax_amount"] == 6744.0
    assert norm["confidence_score"] == 1.0
    assert scan_json["versions"][0]["modified_by"] == "SII_TED_PDF417"

