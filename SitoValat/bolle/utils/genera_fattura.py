
from django.utils.xmlutils import SimplerXMLGenerator
from ..models import RigaFattura
from .genera_pdf import genera_pdf_base64
from xml.dom.minidom import parseString
from io import StringIO
from django.db.models import Sum

def genera_fattura_xml(fattura):

    output = StringIO()
    xml = SimplerXMLGenerator(output, 'utf-8')
    xml.startDocument()

    xml.startElement("p:FatturaElettronica", {
        "versione": "FPR12",
        "xmlns:ds": "http://www.w3.org/2000/09/xmldsig#",
        "xmlns:p": "http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"
    })

    righe = RigaFattura.objects.filter(fattura = fattura)
    # FatturaElettronicaHeader
    xml.startElement("FatturaElettronicaHeader", {})
    xml.startElement("DatiTrasmissione", {})
    xml.startElement("IdTrasmittente", {})
    xml.addQuickElement("IdPaese", "IT")
    if fattura.concessionario.codice_fiscale:
        xml.addQuickElement("IdCodice", fattura.concessionario.codice_fiscale)
    else:
        xml.addQuickElement("IdCodice", fattura.concessionario.partita_iva)
    xml.endElement("IdTrasmittente")
    xml.addQuickElement("ProgressivoInvio", fattura.numero)
    xml.addQuickElement("FormatoTrasmissione", "FPR12") # FPR12 Fatture verso Privati - FPA12 Fattura verso PA
    if fattura.cliente.cod_dest != "0000000":
        xml.addQuickElement("CodiceDestinatario", fattura.cliente.cod_dest)
    else:
        xml.addQuickElement("CodiceDestinatario", "0000000")
        xml.addQuickElement("PECDestinatario", fattura.cliente.pec)
    xml.endElement("DatiTrasmissione")

    xml.startElement("CedentePrestatore", {})
    xml.startElement("DatiAnagrafici", {})
    xml.startElement("IdFiscaleIVA", {})
    xml.addQuickElement("IdPaese", "IT")
    xml.addQuickElement("IdCodice", fattura.concessionario.partita_iva)
    xml.endElement("IdFiscaleIVA")
    xml.addQuickElement("CodiceFiscale", fattura.concessionario.codice_fiscale)  # Codice fiscale come partita IVA
    xml.startElement("Anagrafica", {})
    xml.addQuickElement("Denominazione", fattura.concessionario.nome)
    xml.endElement("Anagrafica")
    xml.addQuickElement("RegimeFiscale", "RF01") # RF01 regime fiscale Ordinario
    xml.endElement("DatiAnagrafici")
    xml.startElement("Sede", {})
    xml.addQuickElement("Indirizzo", fattura.concessionario.indirizzo)
    xml.addQuickElement("CAP", fattura.concessionario.cap)
    xml.addQuickElement("Comune", fattura.concessionario.citta)  # Dati statici o aggiungibili
    xml.addQuickElement("Provincia", fattura.concessionario.provincia)
    xml.addQuickElement("Nazione", "IT")
    xml.endElement("Sede")
    xml.endElement("CedentePrestatore")

    xml.startElement("CessionarioCommittente", {})
    xml.startElement("DatiAnagrafici", {})
    xml.startElement("IdFiscaleIVA", {})
    xml.addQuickElement("IdPaese", "IT")
    xml.addQuickElement("IdCodice", fattura.cliente.piva)
    xml.endElement("IdFiscaleIVA")
    xml.startElement("Anagrafica", {})
    xml.addQuickElement("Denominazione", fattura.cliente.nome)
    xml.endElement("Anagrafica")
    xml.endElement("DatiAnagrafici")
    xml.startElement("Sede", {})
    xml.addQuickElement("Indirizzo", fattura.cliente.via)
    xml.addQuickElement("CAP", fattura.cliente.cap)
    xml.addQuickElement("Comune", fattura.cliente.citta)
    xml.addQuickElement("Provincia", fattura.cliente.provincia)
    xml.addQuickElement("Nazione", "IT")
    xml.endElement("Sede")
    xml.endElement("CessionarioCommittente")
    xml.endElement("FatturaElettronicaHeader")

    # FatturaElettronicaBody
    xml.startElement("FatturaElettronicaBody", {})
    xml.startElement("DatiGenerali", {})
    xml.startElement("DatiGeneraliDocumento", {})
    xml.addQuickElement("TipoDocumento", fattura.tipo_fattura.tipo) #Da modificare in base al tipo documento della fattura
    xml.addQuickElement("Divisa", "EUR")
    xml.addQuickElement("Data", fattura.data.strftime('%Y-%m-%d'))
    xml.addQuickElement("Numero", fattura.numero)
    xml.addQuickElement("ImportoTotaleDocumento", str(format(fattura.totali["tot"], ".2f"))) # Importo Totale Documento
    #if fattura.totali["arr"] != 0:
    #    xml.addQuickElement("Arrotondamento", str(fattura.totali["arr"]))
    xml.endElement("DatiGeneraliDocumento")
    xml.endElement("DatiGenerali")

    xml.startElement("DatiBeniServizi", {})
    linea = 1
    imp4=0
    imp10=0
    imp22=0
    for riga in righe:
        # DEBUG print(articolo)
        # DEBUG print(dettagli)
        xml.startElement("DettaglioLinee", {})
        xml.addQuickElement("NumeroLinea", riga.numero_linea if riga.numero_linea else str(linea))
        xml.startElement("CodiceArticolo", {})
        xml.addQuickElement("CodiceTipo", "AswArtFor")
        xml.addQuickElement("CodiceValore", riga.articolo.nome)
        xml.endElement("CodiceArticolo")
        xml.addQuickElement("Descrizione", riga.articolo.descrizione)
        xml.addQuickElement("Quantita", str(format(riga.quantita, ".2f"))) # Quantità totale articolo
        xml.addQuickElement("PrezzoUnitario", str(format(riga.prezzo, ".3f"))) # Col punto
        xml.addQuickElement("PrezzoTotale", str(format(riga.imp, ".2f"))) # Prezzo non ivato
        xml.addQuickElement("AliquotaIVA", str(format(riga.iva, ".2f")))
        if riga.iva == 4.00:
            imp4+=riga.prezzo
        elif riga.iva == 10.00:
            imp10+=riga.prezzo
        elif riga.iva == 22.00:
            imp22+=riga.prezzo
        xml.endElement("DettaglioLinee")
        linea += 1
    if imp4 != 0:
        xml.startElement("DatiRiepilogo", {})
        valori = fattura.totali.get("4", {"imp": 0, "iva": 0, "tot": 0})
        xml.addQuickElement("AliquotaIVA", "4.00")
        xml.addQuickElement("ImponibileImporto", format(valori["imp"], ".2f"))
        xml.addQuickElement("Imposta", format(valori["iva"], ".2f"))
        xml.addQuickElement("EsigibilitaIVA", "I")
        xml.addQuickElement("RiferimentoNormativo", "IVA 4%")
        xml.endElement("DatiRiepilogo")
    if imp10 != 0:
        xml.startElement("DatiRiepilogo", {})
        valori = fattura.totali.get("10", {"imp": 0, "iva": 0, "tot": 0})
        xml.addQuickElement("AliquotaIVA", "10.00")
        xml.addQuickElement("ImponibileImporto", format(valori["imp"], ".2f"))
        xml.addQuickElement("Imposta", format(valori["iva"], ".2f"))
        xml.addQuickElement("EsigibilitaIVA", "I")
        xml.addQuickElement("RiferimentoNormativo", "IVA 10%")
        xml.endElement("DatiRiepilogo")
    if imp22 != 0:
        xml.startElement("DatiRiepilogo", {})
        valori = fattura.totali.get("22", {"imp": 0, "iva": 0, "tot": 0})
        xml.addQuickElement("AliquotaIVA", "22.00")
        xml.addQuickElement("ImponibileImporto", format(valori["imp"], ".2f"))
        xml.addQuickElement("Imposta", format(valori["iva"], ".2f"))
        xml.addQuickElement("EsigibilitaIVA", "I")
        xml.addQuickElement("RiferimentoNormativo", "IVA 22%")
        xml.endElement("DatiRiepilogo")
    xml.endElement("DatiBeniServizi")
    # Dati Pagamento
    if fattura.modalita_pagamento == "MP05":
        xml.startElement("DatiPagamento", {})
        xml.addQuickElement("CondizioniPagamento", fattura.condizioni_pagamento)
        xml.startElement("DettaglioPagamento", {})
        xml.addQuickElement("ModalitaPagamento", fattura.modalita_pagamento)
        xml.addQuickElement("DataRiferimentoTerminiPagamento", fattura.data.strftime('%Y-%m-%d'))
        xml.addQuickElement("DataScadenzaPagamento", fattura.scadenza_pagamento.strftime('%Y-%m-%d'))
        xml.addQuickElement("ImportoPagamento", format(fattura.totali["tot"], ".2f"))
        xml.addQuickElement("IstitutoFinanziario", fattura.concessionario.istituto_finanziario)
        xml.addQuickElement("IBAN", fattura.concessionario.iban)
        xml.endElement("DettaglioPagamento")
        xml.endElement("DatiPagamento")
    else:
        xml.startElement("DatiPagamento", {})
        xml.addQuickElement("CondizioniPagamento", fattura.condizioni_pagamento)
        xml.startElement("DettaglioPagamento", {})
        xml.addQuickElement("ModalitaPagamento", fattura.modalita_pagamento)
        xml.addQuickElement("DataRiferimentoTerminiPagamento", fattura.data.strftime('%Y-%m-%d'))
        xml.addQuickElement("DataScadenzaPagamento", fattura.scadenza_pagamento.strftime('%Y-%m-%d'))
        xml.addQuickElement("ImportoPagamento", format(fattura.totali["tot"], ".2f"))
        xml.addQuickElement("IstitutoFinanziario", fattura.concessionario.istituto_finanziario)
        xml.endElement("DettaglioPagamento")
        xml.endElement("DatiPagamento")
    base64 = genera_pdf_base64(fattura)
    if base64:
        xml.startElement("Allegati", {})
        xml.addQuickElement("NomeAttachment", str(fattura.tipo_fattura.descrizione) + " N" + str(fattura.numero)+ ".pdf")
        xml.addQuickElement("FormatoAttachment", "PDF")
        xml.addQuickElement("DescrizioneAttachment", str(fattura.tipo_fattura.descrizione) + " del " + str(fattura.data) + " N. " + str(fattura.numero) + ", emessa da " + str(fattura.concessionario.nome))
        xml.addQuickElement("Attachment", base64)
        xml.endElement("Allegati")

    xml.endElement("FatturaElettronicaBody")
    xml.endElement("p:FatturaElettronica")
    xml.endDocument()

    # Recupera il contenuto XML
    raw = output.getvalue()
    dom = parseString(raw)
    formattato = dom.toprettyxml(indent="    ")
    return formattato