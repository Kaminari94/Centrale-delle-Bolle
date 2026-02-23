from django.core.management.base import BaseCommand
from ...models import *
import csv

class Command(BaseCommand):
    help = "Export carichi data"

    def handle(self, *args, **kwargs):
        carichi = Carico.objects.prefetch_related("righe").all()
        resi = Reso.objects.prefetch_related("righe").all()

        with open("exports/carichi.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["data","cod_zona","cod_prodotto","carico_qnt"])
            for carico in carichi:
                for riga in carico.righe.all():
                    writer.writerow([
                        carico.data,
                        carico.zona_id,
                        riga.articolo.nome,
                        riga.quantita
                    ])

        with open("exports/rimanenze.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["data","cod_zona","cod_prodotto","reso_qnt"])
            for reso in resi:
                for riga in reso.righe.all():
                    writer.writerow([
                        reso.data,
                        reso.zona_id,
                        riga.articolo.nome,
                        riga.quantita
                    ])