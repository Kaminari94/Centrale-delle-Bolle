from .formatters import (
    WIDTH,
    COL_CODE,
    COL_DESC,
    COL_QTY,
    separator,
    pad_left,
    pad_right,
    wrap_text,
)


def render_ddt(bolla):
    lines = []

    # HEADER
    concessionario = bolla.cliente.concessionario.nome if bolla.cliente.concessionario else ""
    lines.append(pad_right(concessionario, WIDTH))

    tipo = bolla.tipo_documento.nome if bolla.tipo_documento else ""
    numero = f"Bolla N: {bolla.numero}/{tipo}"
    lines.append(pad_right(numero, WIDTH))

    data_str = bolla.data.strftime("%d/%m/%Y %H:%M")
    lines.append(pad_right(f"Data: {data_str}", WIDTH))

    lines.append(pad_right(f"Cliente: {bolla.cliente.nome}", WIDTH))
    lines.append(separator())

    # intestazione colonne
    header = (
        pad_right("Cod", COL_CODE)
        + " "
        + pad_right("Descrizione", COL_DESC)
        + " "
        + pad_left("Qnt", COL_QTY)
    )
    lines.append(header)
    lines.append(separator())

    # RIGHE
    righe = bolla.righe.all()

    for riga in righe:
        codice = riga.articolo.nome  # nel tuo sistema è il codice 600xxx
        descrizione = riga.articolo.descrizione
        quantita = riga.quantita
        lotto = riga.lotto

        desc_wrapped = wrap_text(descrizione, COL_DESC)

        # prima riga con quantità
        first_desc = desc_wrapped[0] if desc_wrapped else ""
        main_row = (
            pad_right(codice, COL_CODE)
            + " "
            + pad_right(first_desc, COL_DESC)
            + " "
            + pad_left(quantita, COL_QTY)
        )
        lines.append(main_row)

        # righe successive descrizione
        for extra_line in desc_wrapped[1:]:
            continuation = (
                " " * COL_CODE
                + " "
                + pad_right(extra_line, COL_DESC)
                + " "
                + " " * COL_QTY
            )
            lines.append(continuation)

        # riga lotto
        if lotto:
            lotto_line = (
                " " * COL_CODE
                + " "
                + pad_right(f"Lotto: {lotto}", COL_DESC)
            )
            lines.append(lotto_line)

    lines.append(separator())
    lines.append(pad_right("Timbro e Firma:", WIDTH))

    return "\n".join(lines)