import os
from datetime import time, datetime

def export_file(file_path):
    """
    Esporta i dati delle bolle, clienti e articoli in un file di testo per la Centrale
    :param bolle: le bolle da esportare nel
    """

    #inizializza linee file. Lista di stringhe
    linee = []
    data = datetime.now()

    #Header del file Centrale. AAA010014001 credo sia codice concessionario.
    linee.append("AAA010014001"+data.strftime("%y%m%d")+"                                                                                                                             ")

    clienti = Cliente.objects.all()
    for cliente in clienti:
        linea = (
            f"P{cliente.codice:0>10}"  # Codice cliente (10 caratteri)
            "0000"
            f"{cliente.nome:<35}"    # Nome cliente (35 caratteri)
            f"{cliente.indirizzo:<37}"  # Indirizzo (37 caratteri)
            f"{cliente.cap_citta:<30}"  # CAP e città (22 caratteri)
            f"{cliente.provincia:<2}"   # Provincia (2 caratteri)
            f"{cliente.partita_iva:<14}"  # Partita IVA (14 caratteri)
            f"{cliente.codice_proprietario:0<7}"  # Codice proprietario (7 caratteri)
            "   "
        )
        linee.append(linea)

        # Esporta le bolle
        bolle = Bolla.objects.all()
        for bolla in bolle:
            linea = (
                f"K00{bolla.numero:0>7}"  # Numero bolla (7 caratteri)
                f"{bolla.data.strftime('%d%m%Y'):<8}"  # Data (es 01011994)(8 caratteri)
                f"{bolla.cliente.codice:0>12}"  # Codice cliente (12 caratteri)
                "                                                                                                                 "
            )
            linee.append(linea)

            # Esporta gli articoli della bolla
            righe_bolla = RigaBolla.objects.filter(bolla=bolla)
            for riga in righe_bolla:
                prezzo = int(float(riga.articolo.prezzo)*10)
                linea = (
                    f"K02{bolla.numero:0>7}"  # Numero bolla (7 caratteri)
                    f"{riga.articolo.nome:<20}"  # Codice articolo (20 caratteri)
                    f"{riga.quantita:0>7}"  # Quantità (8 caratteri, allineata a destra)
                    f"{prezzo:0>33}"  # Prezzo (33 caratteri)
                    "                                                                         "
                )
                linee.append(linea)
    # Scrivi il contenuto nel file
    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(line + "\n" for line in linee)

    print(f"Esportazione completata. File salvato in {file_path}")