from bolle.models import Articolo, ArticoliConcessi, RigaBolla, RigaCarico


def parse_quantity(value):
    """Converte una quantita nel formato storico dei flussi rapidi."""
    return int(value)


def allowed_article_ids(cliente):
    return set(
        ArticoliConcessi.objects.filter(proprietario=cliente.proprietario)
        .values_list("articolo", flat=True)
    )


def validate_article_lines(cliente, lines, include_customer_address=False):
    """Restituisce la mappa articoli e gli errori compatibili con le API esistenti."""
    requested_codes = {line["codice"] for line in lines if line["quantita"] != 0}
    articles_by_code = {
        article.nome: article
        for article in Articolo.objects.filter(nome__in=requested_codes)
    }
    allowed_ids = allowed_article_ids(cliente)
    errors = []

    for line in lines:
        if line["quantita"] == 0:
            continue
        article = articles_by_code.get(line["codice"])
        if article is None:
            errors.append({"line": line["line"], "message": f"Articolo errato: {line['codice']}."})
            continue
        if article.pk not in allowed_ids:
            customer_name = cliente.nome
            if include_customer_address:
                customer_name = f"{customer_name} {cliente.via}"
            errors.append({
                "line": line["line"],
                "message": f"Articolo {line['codice']} non concesso a {customer_name}.",
            })

    return articles_by_code, errors


def default_lot_for_article(article):
    last_load_row = RigaCarico.objects.filter(articolo=article).order_by("-carico__data").first()
    return last_load_row.lotto if last_load_row else "---"


def create_bolla_row(bolla, article, quantity, lotto=None):
    if lotto is None:
        lotto = default_lot_for_article(article)
    return RigaBolla.objects.create(
        bolla=bolla,
        articolo=article,
        quantita=quantity,
        lotto=lotto,
    )
