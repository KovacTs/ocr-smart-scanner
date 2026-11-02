import io
from typing import Sequence
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from app.models.scan import Scan


def generate_scans_excel(
    scans: Sequence[Scan], base_url: str = "http://localhost:8000"
) -> bytes:
    """
    Genera un archivo Excel (.xlsx) profesional con formato contable, filtros automáticos,
    fórmulas de sumatoria (=SUM) y enlace directo a la imagen de evidencia en WebP.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Reporte Escaneos OCR"

    headers = [
        "Folio / N°",
        "Tipo Documento",
        "Fecha Emisión",
        "RUT Emisor",
        "Razón Social Emisor",
        "RUT Receptor",
        "Monto Neto",
        "IVA",
        "Monto Exento",
        "Monto Total",
        "Moneda",
        "Score Enfoque",
        "Respaldo Imagen WebP",
    ]

    header_fill = PatternFill(
        start_color="1E293B", end_color="1E293B", fill_type="solid"
    )
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    total_fill = PatternFill(
        start_color="F1F5F9", end_color="F1F5F9", fill_type="solid"
    )
    total_font = Font(name="Calibri", size=11, bold=True, color="0F172A")
    link_font = Font(name="Calibri", size=10, color="2563EB", underline="single")
    thin_border = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1"),
    )

    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    ws.row_dimensions[1].height = 26

    for row_idx, scan in enumerate(scans, start=2):
        norm = scan.current_normalized_data or {}
        issuer = norm.get("issuer", {})
        receiver = norm.get("receiver", {})
        amounts = norm.get("amounts", {})

        folio = norm.get("document_number") or scan.folio or "S/N"
        doc_type = norm.get("document_type") or scan.document_type
        issue_date = norm.get("issue_date") or scan.issue_date or ""
        issuer_rut = issuer.get("tax_id", "")
        issuer_name = issuer.get("legal_name", "")
        receiver_rut = receiver.get("tax_id", "")

        net_val = float(amounts.get("net_amount", 0.0) or 0.0)
        tax_val = float(amounts.get("tax_amount", 0.0) or 0.0)
        exempt_val = float(amounts.get("exempt_amount", 0.0) or 0.0)
        total_val = float(amounts.get("total_amount", scan.total_amount) or 0.0)
        currency = amounts.get("currency", "CLP")
        full_image_url = f"{base_url.rstrip('/')}{scan.image_url}"

        row_values = [
            folio,
            doc_type,
            issue_date,
            issuer_rut,
            issuer_name,
            receiver_rut,
            net_val,
            tax_val,
            exempt_val,
            total_val,
            currency,
            round(float(scan.blur_score or 0.0), 1),
            "Ver Imagen Original",
        ]
        ws.append(row_values)

        for col_idx in range(1, len(headers) + 1):
            c = ws.cell(row=row_idx, column=col_idx)
            c.border = thin_border
            if col_idx in (7, 8, 9, 10):
                c.number_format = "$#,##0"
                c.alignment = Alignment(horizontal="right", vertical="center")
            elif col_idx == 13:
                c.hyperlink = full_image_url
                c.font = link_font
                c.alignment = Alignment(horizontal="center", vertical="center")
            else:
                c.alignment = Alignment(horizontal="left", vertical="center")

    # Fila de Totales con fórmulas =SUM(...)
    total_row = len(scans) + 2
    ws.cell(row=total_row, column=6, value="TOTALES CONSOLIDADOS").font = total_font
    ws.cell(row=total_row, column=6).alignment = Alignment(
        horizontal="right", vertical="center"
    )

    if len(scans) > 0:
        for col_idx in (7, 8, 9, 10):
            col_letter = get_column_letter(col_idx)
            formula_cell = ws.cell(
                row=total_row,
                column=col_idx,
                value=f"=SUM({col_letter}2:{col_letter}{total_row - 1})",
            )
            formula_cell.font = total_font
            formula_cell.fill = total_fill
            formula_cell.number_format = "$#,##0"
            formula_cell.border = thin_border
    else:
        for col_idx in (7, 8, 9, 10):
            c = ws.cell(row=total_row, column=col_idx, value=0)
            c.font = total_font
            c.fill = total_fill
            c.number_format = "$#,##0"
            c.border = thin_border

    # Habilitar autofiltros y ajustar ancho de columnas
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(1, len(scans) + 1)}"
    column_widths = [14, 24, 15, 16, 32, 16, 16, 15, 16, 16, 10, 15, 24]
    for idx, width in enumerate(column_widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()
