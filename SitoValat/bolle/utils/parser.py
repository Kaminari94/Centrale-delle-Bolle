def parse_file(file_path):
    data = {
        "header": None,
        "clienti": [],
        "bolle": [],
        "articoli": [],
    }

    with open(file_path, 'r') as infile:
        for line in infile:
            line = line.strip()
            if line.startswith("AAA"):
                data["header"] = line  #Per ora salviamo la riga completa, la prima riga. Non so che cos'è
            elif line.startswith("P"):
                cliente = {
                    "codice_cliente": line[1:11].strip("0"),
                    "nome_cliente": line[15:50].strip(),
                    "indirizzo": line[50:87].strip(),
                    "cap": line[87:92].strip(),
                    "citta": line[92:112].strip(),
                    "prov":line[117:119].strip(),
                    "partita_iva":line[119:130].strip(),
                    "codice_proprietario": line[133:].strip("0"),
                }
                data["clienti"].append(cliente)
            elif line.startswith("K000"):
                bolla = {
                    "numero_bolla" :line[1:10].lstrip("0"),
                    "data" :line[10:18].strip(),
                    "codice_cliente" :line[20:30].strip("0"),
                }
                data["bolle"].append(bolla)
            elif line.startswith("K02"):
                articolo = {
                    "numero_bolla" :line[3:10].lstrip("0"),
                    "codice_articolo" :line[10:30].strip(),
                    "quantita" :int(line[30:37].strip()),
                    "campo_sconosciuto" :line[38:].strip("0"), #non so che è. Sta sto numero ogni fine bolla.
                    # Forse è il prezzo, 171 e 103 ce l'hanno uguale. Anche i mezzi litri
                    # mezzi litri : 092  (forse 0.92?)
                    # 171 e 103: 166   (forse 1.66?)
                }
                data["articoli"].append(articolo)

    return data

if __name__ == "__main__":
    data = parse_file(r"E:\Desktop\VALAT\014-CESSIONE-250108")
    print(data["header"])
    for cliente in data["clienti"]:
        print(cliente)
    for bolla in data["bolle"]:
        print(bolla)
    for articolo in data["articoli"]:
        print(articolo)
#    print("Quanti clienti so? Ecco qua:", len(data["clienti"]))
#    print("Quante bolle so? Ecco qua:", len(data["bolle"]))
#    print("Quante articoli so? Ecco qua:", len(data["articoli"]))