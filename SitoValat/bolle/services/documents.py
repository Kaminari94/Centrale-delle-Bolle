from django.db import transaction

from bolle.models import Bolla
from .rows import create_bolla_row


def create_bolla_with_lines(cliente, lines, articles_by_code):
    """Crea una bolla e le sue righe validate in un'unica transazione."""
    with transaction.atomic():
        bolla = Bolla.objects.create(
            cliente=cliente,
            tipo_documento=cliente.tipo_documento_predefinito,
        )
        for line in lines:
            if line["quantita"] == 0:
                continue
            create_bolla_row(bolla, articles_by_code[line["codice"]], line["quantita"])
    return bolla


def replace_bolla_lines(bolla, lines, articles_by_code):
    """Sostituisce le righe mantenendo il lotto della prima riga gia presente per articolo."""
    existing_lots = {
        row.articolo.nome: row.lotto
        for row in bolla.righe.select_related("articolo")
    }

    with transaction.atomic():
        bolla.righe.all().delete()
        for line in lines:
            if line["quantita"] == 0:
                continue
            article = articles_by_code[line["codice"]]
            create_bolla_row(
                bolla,
                article,
                line["quantita"],
                lotto=existing_lots.get(line["codice"]),
            )
    return bolla
