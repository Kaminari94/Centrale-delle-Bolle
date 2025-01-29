from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from .models import RigaFattura

@receiver(post_delete, sender=RigaFattura)
def aggiorna_totali_fattura_dopo_eliminazione(sender, instance, **kwargs):
    fattura = instance.fattura
    if fattura:  # Controlla che la fattura esista
        fattura.aggiorna_totali()
        fattura.save()

@receiver(post_save, sender=RigaFattura)
def aggiorna_totali_fattura_dopo_inserimento_o_modifica(sender, instance, created, **kwargs):
    fattura = instance.fattura
    if fattura:  # Controlla che la fattura esista
        fattura.aggiorna_totali()
        fattura.save()