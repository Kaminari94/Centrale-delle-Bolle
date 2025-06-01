import io
import base64
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import locale

locale.setlocale(locale.LC_ALL, 'it_IT.UTF-8')

def genera_pdf_base64(fattura):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setTitle("Fattura N. {}".format(fattura.numero) + " del {}".format(fattura.data.strftime("%d/%m/%y"))  + " " + "{}".format(fattura.cliente.nome))
    #Dimensioni pagina
    larghezza, altezza = A4
    # Fornitore (Concessionario quindi)
    logo_path = fattura.concessionario.logo.path
    max_righe_per_pagina = 38
    y_footer = 153.8897637795277

    def disegna_intestazione():
        c.drawImage(logo_path, 43, altezza - 130, width=200, height=100)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, altezza - 140, fattura.concessionario.nome)
        c.setFont("Helvetica", 12)
        c.drawString(50, altezza - 150, fattura.concessionario.via)
        c.drawString(50, altezza - 160,
                     fattura.concessionario.cap + " " + fattura.concessionario.citta + " " + fattura.concessionario.provincia)
        c.drawString(50, altezza - 170, str(fattura.concessionario.telefono))
        c.drawString(50, altezza - 180, "P.IVA: " + fattura.concessionario.partita_iva)

        # Dettagli del cliente
        c.setFont("Helvetica-Bold", 12)
        c.drawRightString(larghezza - 50, altezza - 97, "Cliente:")
        c.setFont("Helvetica", 12)
        c.drawRightString(larghezza - 50, altezza - 110, fattura.cliente.nome)
        c.drawRightString(larghezza - 50, altezza - 120, fattura.cliente.indirizzo)
        c.drawRightString(larghezza - 50, altezza - 130, fattura.cliente.via)
        c.drawRightString(larghezza - 50, altezza - 140,
                          fattura.cliente.cap + " " + fattura.cliente.citta + " " + fattura.cliente.provincia)
        c.drawRightString(larghezza - 50, altezza - 150, "P.IVA: " + fattura.cliente.piva)
        c.setLineWidth(1)  # Imposta spessore linea cliente
        c.line(390, altezza - 160, 550, altezza - 160)  # Linea per cliente
        c.setLineWidth(2)  # Imposta spessore linea
        c.line(50, altezza - 200, 550, altezza - 200)  # Linea per tutto il foglio
        # Dettagli documento tipo numero, data, codici vari boh
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, altezza - 210, "Tipo documento")
        c.drawString(180, altezza - 210, "Numero documento")
        c.drawString(320, altezza - 210, "Data documento")
        c.setFont("Helvetica", 10)
        c.drawString(50, altezza - 220, "{}".format(fattura.tipo_fattura))
        c.drawString(180, altezza - 220, "{}".format(fattura.numero))
        c.drawString(320, altezza - 220, "{}".format(fattura.data.strftime("%d/%m/%y")))
        if fattura.cliente.cod_dest != "0000000":
            c.setFont("Helvetica-Bold", 10)
            c.drawString(458, altezza - 210, "Codice destinatario")
            c.setFont("Helvetica", 10)
            c.drawString(458, altezza - 220, "{}".format(fattura.cliente.cod_dest))
        else:
            c.setFont("Helvetica-Bold", 10)
            c.drawRightString(larghezza - 50, altezza - 210, "Indirizzo email pec:")
            c.setFont("Helvetica", 8)
            c.drawRightString(larghezza - 50, altezza - 220, "{}".format(fattura.cliente.pec))
        c.setFont("Helvetica", 10)

    def disegna_footer(y):
        c.setLineWidth(1)  # Imposta spessore linea
        c.line(50, y - 20, 550, y - 20)  # Linea per tutto il foglio
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y - 40, "Imponibile 4%: ")
        c.drawString(230, y - 40, "Imponibile 10%: ")
        c.drawRightString(500, y - 40, "Imponibile 22%: ")
        c.drawString(83, y - 60, "IVA 4%: ")
        c.drawString(263, y - 60, "IVA 10%: ")
        c.drawRightString(500, y - 60, "IVA 22%: ")
        c.setFont("Helvetica", 10)
        c.drawString(135, y - 40, locale.format_string("€ %.3f", fattura.totali["4"]["imp"], grouping=True))
        c.drawString(325, y - 40, locale.format_string("€ %.3f", fattura.totali["10"]["imp"], grouping=True))
        c.drawRightString(550, y - 40, locale.format_string("€ %.3f", fattura.totali["22"]["imp"], grouping=True))
        c.drawString(135, y - 60, locale.format_string("€ %.3f", fattura.totali["4"]["iva"], grouping=True))
        c.drawString(325, y - 60, locale.format_string("€ %.3f", fattura.totali["10"]["iva"], grouping=True))
        c.drawRightString(550, y - 60, locale.format_string("€ %.3f", fattura.totali["22"]["iva"], grouping=True))

        # Totale finale
        c.setLineWidth(2)  # Imposta spessore linea
        c.line(50, y - 90, 550, y - 90)  # Linea per tutto il foglio
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y - 100, "Contributo ambientale CONAI assolto ove dovuto.")
        c.setFont("Helvetica-Bold", 14)
        c.drawString(400, y - 110, "Totale:")
        c.drawRightString(550, y - 110, locale.format_string("€ %.2f", fattura.totali["tot"], grouping=True))

    def disegna_tabella_articoli(y):
        # Intestaziona tabella con i prodotti
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, altezza - 250, "Codice")
        c.drawString(110, altezza - 250, "Descrizione")
        c.drawString(260, altezza - 250, "IVA")
        c.drawString(300, altezza - 250, "Quantità")
        c.drawString(400, altezza - 250, "Prezzo")
        c.drawRightString(550, altezza - 250, "Imponibile")

    disegna_intestazione()
    y = altezza-250
    disegna_tabella_articoli(y)
    y -= 20

    # Righe della fattura
    riga_counter = 0

    for riga in fattura.righe.all():
        if riga_counter >= max_righe_per_pagina:
            disegna_footer(y_footer)
            c.showPage()
            disegna_intestazione()
            y = altezza-250
            disegna_tabella_articoli(y)
            y -= 20
            riga_counter = 0 #reset contatore righe
        #Disegna dati della tabella, i vari prodotti
        c.setFont("Helvetica", 10)
        c.setLineWidth(1)  # Imposta spessore linea cliente
        c.drawString(50, y, riga.articolo.nome)
        c.drawString(110, y, riga.articolo.descrizione)
        if riga.articolo.categoria.nome == "Diciture":
            c.line(50, y - 2, 550, y - 2)  # Linea per cliente
            y -= 11
            riga_counter += 1
            continue
        c.drawString(260, y, str(riga.iva)+"%")
        c.drawRightString(350, y, str(riga.quantita))

        c.drawRightString(440, y, locale.format_string("€ %.3f", riga.prezzo, grouping=True))
        c.drawRightString(550, y, locale.format_string("€ %.3f", riga.imp, grouping=True))
        c.line(50, y-2, 550, y-2)  # Linea per cliente
        y -= 11
        riga_counter += 1

    disegna_footer(y_footer)

    # Salva il PDF
    c.save()

    # Converti il buffer in base64
    pdf_data = buffer.getvalue()
    buffer.close()
    base64_pdf = base64.b64encode(pdf_data).decode('utf-8')

    # Salva il PDF in base64 nel campo del modello
    fattura.pdf_file = base64_pdf
    fattura.save()

    return base64_pdf