import io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import Paragraph, Table, TableStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT


def genera_pdf_bolla(bolla):
    def calc_height():
        # Simula il layout per calcolare l'altezza totale necessaria
        h = 5 * mm  # Margine superiore

        # Intestazione
        h += max(
            len(bolla.tipo_documento.concessionario.nome.split('\n')) * 8 * mm,
            len(bolla.cliente.nome.split('\n')) * 8 * mm
        )
        if 'CLS' in bolla.tipo_documento.nome:
            # Dettagli bolla
            h += 55 * mm
        else:
            h += 35 * mm

        # Tabella articoli
        h += (len(bolla.righe.all()) + 1) * 9 * mm  # +1 per l'header

        # Spazio firma
        h += 60 * mm

        return h
    # Dimensioni documento (48mm di larghezza, altezza dinamica)
    height = calc_height()
    width = 48 * mm
    #height = 200 * mm  # Altezza iniziale grande (verrà ridotta alla fine)

    # Creazione buffer PDF
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))

    # Stili
    styles = getSampleStyleSheet()
    small_style = ParagraphStyle(
        'small',
        parent=styles['Normal'],
        fontSize=7,
        leading=8,
        spaceAfter=2,
        spaceBefore=2
    )
    bold_small_style = ParagraphStyle(
        'bold_small',
        parent=small_style,
        fontName='Helvetica-Bold'
    )
    quantity_style = ParagraphStyle(
        'quantity',
        parent=small_style,
        fontSize=9,
        alignment=TA_RIGHT
    )
    footer_style = ParagraphStyle(
        'footer',
        parent=styles['Normal'],
        fontSize=12,
        alignment=TA_CENTER,
        spaceBefore=15
    )

    # Coordinate iniziali (partiamo dall'alto)
    y_position = height - 2 * mm

    # Intestazione - Concessionario (sinistra)
    concessionario = [
        f"<b>{bolla.tipo_documento.concessionario.nome}</b>",
        bolla.tipo_documento.concessionario.indirizzo,
        f"<b>P. IVA:</b> {bolla.tipo_documento.concessionario.partita_iva}",
        f"<b>Tel:</b> {bolla.tipo_documento.concessionario.telefono}"
    ]

    for line in concessionario:
        p = Paragraph(line, bold_small_style)
        p.wrapOn(c, width / 2, height)
        p.drawOn(c, 0, y_position - p.height)
        y_position -= p.height

    # Intestazione - Cliente (destra)
    y_position = height - 2 * mm  # Ripartiamo dall'alto

    cliente_lines = []
    if 'CLS' in bolla.tipo_documento.nome:
        cliente_lines.append(
            f"<b>Spett.le: {bolla.cliente.proprietario.nome}<br/>Codice: {bolla.cliente.proprietario.codice}</b>")

    cliente_lines += [
        f"<b>Destinazione:<br/>{bolla.cliente.nome}</b>",
        f"<b>Indirizzo:<br/></b>{bolla.cliente.via}",
        f"{bolla.cliente.cap} {bolla.cliente.citta}",
        f"<b>P. IVA:</b> {bolla.cliente.piva}"
    ]

    if 'CLS' in bolla.tipo_documento.nome:
        cliente_lines.append(f"<b>Cod. Dest.:</b> {bolla.cliente.codice}")

    for line in cliente_lines:
        p = Paragraph(line, bold_small_style)
        p.wrapOn(c, width / 2, height)
        p.drawOn(c, width / 2, y_position - p.height)
        y_position -= p.height


    # Dettagli bolla
    if 'CLS' in bolla.tipo_documento.nome:
        y_position -= 5
        # Parte sinistra
        left_details = [
            f"<b>Bolla N°: {bolla.numero} / {bolla.tipo_documento.nome}</b>",
            f"Tipo: {bolla.tipo_documento.descrizione}",
            f"Data: {bolla.data.strftime('%d/%m/%Y %H:%M')}"
        ]
        reset_pos = y_position
        for line in left_details:
            p = Paragraph(line, small_style)
            p.wrapOn(c, width / 2, height)
            p.drawOn(c, 0, y_position - p.height)
            y_position -= p.height

        # Parte destra
        right_details = [
            "<b>Cons. per conto di:</b>",
            bolla.tipo_documento.concessionario.cons_conto.nome,
            bolla.tipo_documento.concessionario.cons_conto.indirizzo
        ]
        y_position = reset_pos
        for line in right_details:
            p = Paragraph(line, small_style)
            p.wrapOn(c, width / 2, height)
            p.drawOn(c, width / 2, y_position - p.height)
            y_position -= p.height
    else:
        y_position -= 10
        # Parte sinistra
        p = Paragraph(f"<b>Data: {bolla.data.strftime('%d/%m/%Y %H:%M')}</b>", small_style)
        p.wrapOn(c, width / 2, height)
        p.drawOn(c, 0, y_position - p.height)

        # Parte destra
        right_details = [
            f"<b>Bolla N°: {bolla.numero} / {bolla.tipo_documento.nome}</b>",
            f"<b>Tipo: {bolla.tipo_documento.descrizione}</b>"
        ]

        for line in right_details:
            p = Paragraph(line, small_style)
            p.wrapOn(c, width / 2, height)
            p.drawOn(c, width / 2, y_position - p.height)
            y_position -= p.height

        #y_position -= p.height

    # Tabella articoli
    y_position -= 3
    col_widths = [13 * mm, 25 * mm, 10 * mm]

    # Intestazione tabella
    headers = ["Cod.", "Descrizione", "Qnt."]
    header_data = [Paragraph(f"<b>{h}</b>", bold_small_style) for h in headers]

    table_data = [header_data]

    # Righe articoli
    for riga in bolla.righe.all():
        row = [
            Paragraph(f"<b>{riga.articolo.nome}</b>", bold_small_style),
            Paragraph(f"{riga.articolo.descrizione}<br/>Lotto:{riga.lotto}", small_style),
            Paragraph(str(riga.quantita), small_style)
        ]
        table_data.append(row)

    # Creazione tabella
    t = Table(
        table_data,
        colWidths=col_widths,
        repeatRows=1
    )

    t.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, colors.black)
    ]))

    # Disegna la tabella
    table_height = 0
    for row in table_data:
        table_height += 9 * mm  # Altezza approssimativa per riga

    t.wrapOn(c, width, height)
    t.drawOn(c, 0, y_position - table_height)
    y_position -= table_height + 9 * mm

    # Spazio per firma
    p = Paragraph("<b>Timbro e Firma:</b>", small_style)
    p.wrapOn(c, width, height)
    p.drawOn(c, 0, y_position - p.height)
    y_position -= p.height + 20 * mm

    # Linea per firma
    c.line(5 * mm, y_position, width - 5 * mm, y_position)
    y_position -= 10 * mm
    # Finalizza PDF
    c.save()
    buffer.seek(0)
    return buffer