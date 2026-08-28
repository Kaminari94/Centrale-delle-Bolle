from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch, Q, Sum
from django.utils import timezone

from ..models import Articolo, Bolla, RigaBolla, RigaCarico, RigaReso, Reso, Zona


class ParametriRiepilogoNonValidi(ValueError):
    pass


def _zona_autorizzata(request, zona_id):
    if not request.user.is_authenticated:
        raise PermissionDenied

    try:
        zona = Zona.objects.select_related("concessionario").get(pk=zona_id)
    except (Zona.DoesNotExist, TypeError, ValueError) as exc:
        raise ParametriRiepilogoNonValidi("La zona inserita non è valida.") from exc

    if request.user.is_superuser:
        return zona

    if hasattr(request.user, "zona"):
        autorizzato = request.user.zona.pk == zona.pk
    elif hasattr(request.user, "concessionario"):
        autorizzato = request.user.concessionario.pk == zona.concessionario_id
    else:
        autorizzato = False

    if not autorizzato:
        raise PermissionDenied

    return zona


def _totali_per_articolo(queryset):
    return {
        riga["articolo_id"]: riga["totale"]
        for riga in queryset.values("articolo_id").annotate(totale=Sum("quantita"))
    }


def calcola_riep_giornaliero(request):
    data_giorno = request.GET.get("data_giorno")
    zona_id = request.GET.get("zona")

    if not data_giorno or not zona_id:
        raise ParametriRiepilogoNonValidi("Inserire data e zona.")

    try:
        data = datetime.strptime(data_giorno, "%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ParametriRiepilogoNonValidi("La data inserita non è valida.") from exc

    zona = _zona_autorizzata(request, zona_id)
    giorno = data.date()
    giorno_precedente = giorno - timedelta(days=1)
    fuso_orario = timezone.get_current_timezone()

    data_inizio = timezone.make_aware(
        datetime.combine(giorno, datetime.min.time()), fuso_orario
    )
    data_fine = timezone.make_aware(
        datetime.combine(giorno, datetime.max.time()), fuso_orario
    )
    prec_inizio = timezone.make_aware(
        datetime.combine(giorno_precedente, datetime.min.time()), fuso_orario
    )
    prec_fine = timezone.make_aware(
        datetime.combine(giorno_precedente, datetime.max.time()), fuso_orario
    )

    ultima_data_reso = (
        Reso.objects.filter(zona=zona, data__lt=giorno)
        .order_by("-data")
        .values_list("data", flat=True)
        .first()
    )

    carichi_precedenti = _totali_per_articolo(
        RigaCarico.objects.filter(
            carico__data=giorno_precedente,
            carico__zona=zona,
        )
    )
    resi_precedenti = (
        _totali_per_articolo(
            RigaReso.objects.filter(
                reso__data=ultima_data_reso,
                reso__zona=zona,
            )
        )
        if ultima_data_reso is not None
        else {}
    )
    resi_attuali = _totali_per_articolo(
        RigaReso.objects.filter(reso__data=giorno, reso__zona=zona)
    )

    righe_bolla = RigaBolla.objects.select_related("articolo")
    bolle_del_giorno = (
        Bolla.objects.filter(
            Q(data__range=(data_inizio, data_fine))
            | Q(
                data__range=(prec_inizio, prec_fine),
                note__icontains="conto domani",
            ),
            cliente__zona=zona,
        )
        .exclude(tipo_documento__nome="RF")
        .select_related("cliente__tipo_documento_predefinito", "tipo_documento")
        .prefetch_related(Prefetch("righe", queryset=righe_bolla))
    )

    totali_bolle = defaultdict(int)
    totali_nt = defaultdict(int)
    totali_cls = defaultdict(int)
    documenti = []

    for bolla in bolle_del_giorno:
        documento = {
            "cliente": f"{bolla.cliente.nome}",
            "tipo": f"{bolla.cliente.tipo_documento_predefinito.nome}",
            "righe": [],
        }

        for riga in bolla.righe.all():
            documento["righe"].append(
                {"articolo": f"{riga.articolo.nome}", "quantita": f"{riga.quantita}"}
            )
            totali_bolle[riga.articolo_id] += riga.quantita
            if bolla.tipo_documento.nome == "NT":
                totali_nt[riga.articolo_id] += riga.quantita
            elif bolla.tipo_documento.nome == "CLS":
                totali_cls[riga.articolo_id] += riga.quantita

        documenti.append(documento)

    riepilogo = {}
    totale = Decimal("0")

    for articolo in Articolo.objects.all():
        carico_prec = carichi_precedenti.get(articolo.pk, 0)
        reso_prec = resi_precedenti.get(articolo.pk, 0)
        carico_totale = carico_prec + reso_prec
        reso_att = resi_attuali.get(articolo.pk, 0)
        bolla_totale = totali_bolle[articolo.pk]
        quantita_venduta = carico_totale - bolla_totale - reso_att
        tot_euro = Decimal(quantita_venduta) * articolo.prezzo_ivato
        tot_euro_formattato = format(tot_euro, ".2f")
        totale += Decimal(tot_euro_formattato)

        riepilogo[articolo.nome] = {
            "nome": articolo.nome,
            "carico_prec": carico_prec,
            "reso_prec": reso_prec,
            "carico_tot": carico_totale,
            "reso_att": reso_att,
            "bolla_nt": totali_nt[articolo.pk],
            "bolla_cls": totali_cls[articolo.pk],
            "bolla_totale": bolla_totale,
            "quantita_venduta": quantita_venduta,
            "tot_euro": tot_euro_formattato,
        }

    return {
        "riepilogo": riepilogo,
        "bolle_del_giorno": bolle_del_giorno,
        "documenti": documenti,
        "totale": totale,
        "zona": zona,
        "data": data,
    }
