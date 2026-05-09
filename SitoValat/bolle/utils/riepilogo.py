from datetime import datetime, timedelta
from django.db.models import Sum, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ..models import Zona, Reso, Carico, Bolla, Articolo, RigaCarico, RigaReso, RigaBolla, ArticoliConcessi, \
    Proprietario


def calcola_riep_giornaliero(request):
    data_giorno = request.GET.get('data_giorno')  # Ottieni la data dalla query string
    zona_id = request.GET.get('zona')  # Ottieni la zona dalla query string

    # Se i parametri non sono forniti, gestisci l'errore e manda indietro
    if not data_giorno or not zona_id:
        print("Nessuna data o nessuna zona. Ma che stai cumbinann")

    # Converti la data e verifica che la zona esista
    data = datetime.strptime(data_giorno, "%Y-%m-%d")
    data_inizio = datetime.combine(data, datetime.min.time())
    data_inizio = timezone.make_aware(data_inizio, timezone.get_current_timezone())
    data_fine = datetime.combine(data, datetime.max.time())
    data_fine = timezone.make_aware(data_fine, timezone.get_current_timezone())
    zona = get_object_or_404(Zona, pk=zona_id)
    giorno_precedente = data - timedelta(days=1)

    carico_giorno_precedente = Carico.objects.filter(data=giorno_precedente, zona=zona)
    reso_giorno_precedente = Reso.objects.filter(data=giorno_precedente, zona=zona)

    i = 0
    while not reso_giorno_precedente:
        reso_giorno_precedente = Reso.objects.filter(data=(giorno_precedente - timedelta(days=i)), zona=zona)
        if not reso_giorno_precedente:
            i += 1

    #if not reso_giorno_precedente:
    #    reso_giorno_precedente = Reso.objects.filter(data=(giorno_precedente - timedelta(days=1)), zona=zona)
    #    if not reso_giorno_precedente:
    #        reso_giorno_precedente = Reso.objects.filter(data=(giorno_precedente - timedelta(days=2)), zona=zona)
    # Rozzo? Si. Ma efficace. Statt zitt.
    prec_inizio = datetime.combine(giorno_precedente, datetime.min.time()) #devo farlo sempre perchè è una datetime, per il range.
    prec_inizio = timezone.make_aware(prec_inizio, timezone.get_current_timezone())
    prec_fine = datetime.combine(giorno_precedente, datetime.max.time())
    prec_fine = timezone.make_aware(prec_fine, timezone.get_current_timezone())

    bolle_del_giorno = Bolla.objects.filter(Q(data__range=(data_inizio, data_fine)) |
        Q(data__range=(prec_inizio, prec_fine), note__icontains="conto domani"),
        tipo_documento__concessionario=zona.concessionario
    ).exclude(tipo_documento__nome="RF")

    documenti = []
    for bolla in bolle_del_giorno:
        righe = RigaBolla.objects.filter(bolla=bolla)
        documento = {"cliente": f"{bolla.cliente.nome}", "tipo":f"{bolla.cliente.tipo_documento_predefinito.nome}"}
        documento["righe"] = []
        for riga in righe:
            oggetto = {"articolo": f"{riga.articolo.nome}", "quantita": f"{riga.quantita}"}
            documento["righe"].append(oggetto)
        documenti.append(documento)

    reso_del_giorno = Reso.objects.filter(data=data, zona=zona)

    articoli = Articolo.objects.all()
    riepilogo = {}
    for articolo in articoli:
        carico_prec = 0
        reso_prec = 0
        nome_art = ""
        carico_totale = 0
        reso_att = 0
        bolla_totale = 0
        quantita_venduta = 0
        nome_art = articolo.nome
        carico_prec = RigaCarico.objects.filter(carico__in=carico_giorno_precedente, articolo=articolo).aggregate(Sum("quantita"))['quantita__sum'] or 0
        reso_prec = RigaReso.objects.filter(reso__in=reso_giorno_precedente, articolo=articolo).aggregate(Sum("quantita"))['quantita__sum'] or 0
        carico_totale = carico_prec + reso_prec
        reso_att = (RigaReso.objects.filter(reso__in=reso_del_giorno, articolo=articolo).aggregate(Sum("quantita")))['quantita__sum'] or 0
        bolla_totale = RigaBolla.objects.filter(bolla__in=bolle_del_giorno, articolo=articolo).aggregate(Sum("quantita"))['quantita__sum'] or 0

        bolla_nt = RigaBolla.objects.filter(bolla__in=bolle_del_giorno, articolo=articolo, bolla__tipo_documento__nome="NT").aggregate(Sum("quantita"))[
            'quantita__sum'] or 0
        bolla_cls = RigaBolla.objects.filter(bolla__in=bolle_del_giorno, articolo=articolo, bolla__tipo_documento__nome="CLS").aggregate(Sum("quantita"))[
            'quantita__sum'] or 0

        quantita_venduta = carico_totale - bolla_totale - reso_att
        prezzo = Articolo.objects.filter(nome=nome_art).first().prezzo_ivato
        tot_euro = float(quantita_venduta * prezzo)
        #if tot_euro == 0:
        #    continue

        riepilogo[nome_art] = {
            "nome" : nome_art,
            "carico_prec" : carico_prec,
            "reso_prec" : reso_prec,
            "carico_tot" : carico_totale,
            "reso_att" : reso_att,
            "bolla_nt" : bolla_nt,
            "bolla_cls" : bolla_cls,
            "bolla_totale" : bolla_totale,
            "quantita_venduta" : quantita_venduta,
            "tot_euro" : format(tot_euro, ".2f")
        }

    totale = 0
    for articolo in riepilogo:
        totale += float(riepilogo[articolo].get("tot_euro"))

    return {
        "riepilogo":riepilogo,
        "bolle_del_giorno":bolle_del_giorno,
        "documenti" : documenti,
        "totale": totale,
        "zona": zona,
        "data": data,
    }