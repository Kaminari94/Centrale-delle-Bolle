import re
import pandas as pd
import base64
from PyPDF2 import PdfMerger

def clean_p7m(p7m_path, output_pdf_path):
    """Estrae PDF da .p7m"""
    with open(p7m_path, "rb") as f:
        data = f.read()
    pdf_start = data.find(b"<Attachment>")
    pdf_end = data.find(b"</Attachment>")
    if pdf_start == -1 or pdf_end == -1:
        raise ValueError("Nessun PDF trovato nel file .p7m!")

    base64_pdf = data[pdf_start + len(b"<Attachment>"):pdf_end].strip()

    with open(output_pdf_path, "wb") as f:
        f.write(base64.b64decode(base64_pdf))

def extract_pdf_from_xml(xml_path, output_pdf_path):
    """Estrae PDF da XML con Base64"""
    import xml.etree.ElementTree as ET
    tree = ET.parse(xml_path)
    root = tree.getroot()
    attachment = root.find(".//Attachment")
    if attachment is None:
        raise ValueError("Tag <Attachment> non trovato!")
    pdf_base64 = attachment.text.strip()
    with open(output_pdf_path, "wb") as f:
        f.write(base64.b64decode(pdf_base64))

def merge_pdfs(pdf_paths, output_merged_pdf):
    """Unisce più PDF in uno"""
    merger = PdfMerger()
    for pdf_path in pdf_paths:
        merger.append(pdf_path)
    merger.write(output_merged_pdf)
    merger.close()

def parse_fattura_pdf(testo_pdf):
    """Parsing del testo della fattura PDF"""
    # Espressioni regolari per estrarre i dati
    regex_bolla = r"NS\.RIF\.:\s*DDT\.(\d+)\s+DEL\s+(\d{2}/\d{2}/\d{2})"
    regex_articolo = r"^(R\s+\d+)\s+(.+?)\s+PZ\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+(\d+)"
    regex_sezioni = r"Merce a \d+ VA\.LAT di VASSO DONATO VIA DELLE INDUSTRIE, 14 BELLIZZI"
    bolle = []
    sezioni = re.split(fr'(?={regex_sezioni})', testo_pdf)
    # Estrai dati bolla
    for sezione in sezioni:
        bolla_matches = list(re.finditer(regex_bolla, sezione))
        if not bolla_matches:
            continue
        for i, match in enumerate(bolla_matches):
            numero_bolla = match.group(1).lstrip("0")
            data_bolla = match.group(2)

        # Estrai articoli
        articoli = []

        for match_art in re.finditer(regex_articolo, sezione, re.MULTILINE):
            if match_art.group(1).startswith("R"):
                articoli.append({
                    "codice_articolo": match_art.group(1).replace(" ", "").lstrip("R"),
                    "quantita": float(match_art.group(3).replace(",", "."))
                })
            else:
                break

        if not articoli:
            raise ValueError("Nessun articolo trovato nella bolla")

        bolla_esistente = None
        for bolla in bolle:
            if bolla["numero_bolla"] == numero_bolla:
                bolla_esistente = bolla
                break
        # Se esiste, aggiungi gli articoli (senza duplicati)
        if bolla_esistente:
            for nuovo_articolo in articoli:
                # Controlla se l'articolo è già presente
                articolo_gia_presente = False
                for articolo in bolla_esistente["articoli"]:
                    if articolo["codice_articolo"] == nuovo_articolo["codice_articolo"]:
                        articolo["quantita"] += nuovo_articolo["quantita"]  # Somma le quantità
                        articolo_gia_presente = True
                        break

                if not articolo_gia_presente:
                    bolla_esistente["articoli"].append(nuovo_articolo)
        else:
            # Altrimenti, crea una nuova bolla
            bolle.append({
                "numero_bolla": numero_bolla,
                "data_bolla": data_bolla,
                "articoli": articoli,
            })

    return bolle

def confronta_fattura_bolle(dati_fattura_lista, df_bolle, df_articoli_bolle):
    """Confronta i dati della fattura PDF con quelli delle bolle"""
    # Inizializza il report complessivo
    report_complessivo = {
        "bolle": [],
        "errori": []
    }

    # Processa ogni bolla nella fattura
    for dati_fattura in dati_fattura_lista:
        bolla_report = {
            "numero_bolla": dati_fattura["numero_bolla"],
            "data_bolla": dati_fattura["data_bolla"],
            "cliente" : df_bolle.loc[df_bolle["numero_bolla"] == dati_fattura["numero_bolla"], "cliente"].values[0],
            "articoli_mancanti_in_fattura": [],
            "articoli_mancanti_in_bolle": [],
            "differenze_quantita": []
        }

        # Filtra bolle per numero bolla
        bolla_corrispondente = df_bolle[
            (df_bolle["numero_bolla"] == dati_fattura["numero_bolla"])
        ]

        if bolla_corrispondente.empty:
            report_complessivo["errori"].append(
                f"Nessuna bolla corrispondente trovata per bolla {dati_fattura['numero_bolla']}")
            continue

        # Filtra articoli della bolla corrispondente
        articoli_bolla = df_articoli_bolle[
            df_articoli_bolle["numero_bolla"] == dati_fattura["numero_bolla"]
            ]

        # Converti articoli fattura in DataFrame
        df_articoli_fattura = pd.DataFrame(dati_fattura["articoli"])

        # Crea dizionari per accesso rapido
        articoli_bolla_dict = {art["codice_articolo"]: art for art in articoli_bolla.to_dict("records")}
        articoli_fattura_dict = {art["codice_articolo"]: art for art in df_articoli_fattura.to_dict("records")}

        # Trova articoli mancanti
        codici_bolla = set(articoli_bolla_dict.keys())
        codici_fattura = set(articoli_fattura_dict.keys())

        bolla_report["articoli_mancanti_in_fattura"] = list(codici_bolla - codici_fattura)
        bolla_report["articoli_mancanti_in_bolle"] = list(codici_fattura - codici_bolla)

        # Confronta quantità per articoli comuni
        for codice in codici_bolla & codici_fattura:
            if codice in ["027110/R", "027110/S"]:
                continue
            qta_bolla = articoli_bolla_dict[codice]["quantita"]
            qta_fattura = articoli_fattura_dict[codice]["quantita"]

            if abs(qta_bolla - qta_fattura) > 0.001:  # Tolleranza per arrotondamenti
                bolla_report["differenze_quantita"].append({
                    "codice_articolo": codice,
                    "quantita_bolla": qta_bolla,
                    "quantita_fattura": qta_fattura,
                    "differenza": qta_fattura - qta_bolla
                })

        report_complessivo["bolle"].append(bolla_report)

    return report_complessivo