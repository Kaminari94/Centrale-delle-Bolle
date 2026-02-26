from datetime import datetime, time, timedelta
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from bolle.models import Bolla, Cliente, Articolo, RigaBolla, RigaCarico, ArticoliConcessi
from .serializers import BollaListSerializer, BollaDetailSerializer, CustomerMiniSerializer
from .receipts.renderer import render_ddt


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"ok": True})

def _normalize_codice(codice: str) -> str:
    codice = (codice or "").strip()

    if codice == "31103":
        return "031103"
    if codice == "31163":
        return "031163"
    if len(codice) == 3:
        return "600" + codice
    if len(codice) == 5:
        return "6" + codice
    return codice

def _apply_customer_scope(qs, user):

    user_conc = getattr(user, "concessionario", None)
    user_zona = getattr(user, "zona", None)

    if user_conc is not None:
        return qs.filter(concessionario=user_conc)
    elif user_zona is not None:
        return qs.filter(zona=user_zona)

    return qs.none()

def _apply_user_scope(qs, user):
    """
    Filtra le bolle in base all'utente.
    - superuser: tutto
    - se user ha attributo 'concessionario': filtra per quel concessionario
    - altrimenti: non filtra (non rompo niente; puoi irrigidire dopo)
    """
    if getattr(user, "is_superuser", False):
        return qs

    user_conc = getattr(user, "concessionario", None)
    user_zona = getattr(user, "zona", None)
    if user_conc is not None:
        return qs.filter(cliente__concessionario=user_conc)
    elif user_zona is not None:
        return qs.filter(cliente__zona=user_zona)

    return qs


def _parse_date_or_today(date_str):
    if not date_str:
        return timezone.localdate()
    try:
        # atteso YYYY-MM-DD
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def bolle_list(request):
    """
    GET /api/bolle/?date=YYYY-MM-DD
    Default: oggi
    """
    date_str = request.query_params.get("date")
    day = _parse_date_or_today(date_str)
    if day is None:
        return Response({"detail": "Parametro 'date' non valido. Usa YYYY-MM-DD."}, status=400)

    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    end = start + timedelta(days=1)

    qs = (
        Bolla.objects
        .select_related("cliente", "tipo_documento")
        .filter(data__gte=start, data__lt=end)
        .order_by("-numero")
    )

    qs = _apply_user_scope(qs, request.user)
    q = request.query_params.get("q")
    if q:
        qs = qs.filter(Q(cliente__nome__icontains=q) | Q(numero__icontains=q))

    data = BollaListSerializer(qs, many=True).data
    return Response({"date": str(day), "count": len(data), "results": data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def bolle_detail(request, pk: int):
    """
    GET /api/bolle/<id>/
    """
    qs = (
        Bolla.objects
        .select_related("cliente", "tipo_documento")
        .prefetch_related("righe__articolo")
    )
    qs = _apply_user_scope(qs, request.user)

    bolla = get_object_or_404(qs, pk=pk)
    data = BollaDetailSerializer(bolla).data
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def receipt_view(request, pk: int):
    """
    GET /api/bolle/<id>/receipt/
    """
    qs = (
        Bolla.objects
        .select_related("cliente", "tipo_documento", "cliente__concessionario")
        .prefetch_related("righe__articolo")
    )
    qs = _apply_user_scope(qs, request.user)

    bolla = get_object_or_404(qs, pk=pk)

    text = render_ddt(bolla)
    return Response({"width": 32, "text": text})

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def customers_list(request):
    """
    GET /api/customers/?q=...
    Ritorna lista clienti per dropdown (id, nome), scoped per user.
    """
    qs = Cliente.objects.all()

    # (opzionale) escludi NTV come fai tu
    qs = qs.exclude(tipo_documento_predefinito__nome="NTV")
    qs = qs.exclude(tipo_documento_predefinito__nome="RF")

    qs = _apply_customer_scope(qs, request.user)

    q = request.query_params.get("q")
    if q:
        qs = qs.filter(nome__icontains=q)

    qs = qs.order_by("nome")

    data = CustomerMiniSerializer(qs, many=True).data
    return Response({"count": len(data), "results": data})

from rest_framework import status

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def bolle_quick_create(request):
    """
    POST /api/bolle/quick/
    Body JSON:
      {
        "customer_id": 12,
        "raw_lines": "127 5\n128 2\n..."
      }

    Success:
      201 { "bolla_id": 123 }

    Validation error:
      400 { "errors": [ { "line": 2, "message": "..." }, ... ] }
    """
    customer_id = request.data.get("customer_id")
    raw_lines = request.data.get("raw_lines", "")

    if customer_id is None:
        return Response({"detail": "customer_id mancante"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        customer_id = int(customer_id)
    except (TypeError, ValueError):
        return Response({"detail": "customer_id non valido"}, status=status.HTTP_400_BAD_REQUEST)

    if not isinstance(raw_lines, str) or not raw_lines.strip():
        return Response({"detail": "raw_lines vuoto"}, status=status.HTTP_400_BAD_REQUEST)

    # ---- 1) Recupero cliente con scope (consigliato) ----
    # Se hai già _apply_customer_scope come ti ho scritto prima, usala qui:
    # qs_clienti = _apply_customer_scope(Cliente.objects.all(), request.user)
    # cliente = get_object_or_404(qs_clienti, pk=customer_id)
    #
    # Se per ora vuoi “replicare” la tua logica vecchia, sostituisci sopra con get_object_or_404 diretto:
    cliente = get_object_or_404(Cliente, pk=customer_id)

    # ---- 2) Parsing righe + controlli di base (come nel tuo codice) ----
    lines = [ln.strip() for ln in raw_lines.strip().split("\n") if ln.strip()]
    errors = []
    parsed = []  # lista di dict: {line, codice_raw, codice_norm, quantita}

    somma = 0
    for idx, ln in enumerate(lines, start=1):
        parts = ln.split()
        if len(parts) != 2:
            errors.append({"line": idx, "message": "Formato non valido. Usa: CODICE QUANTITÀ"})
            continue

        codice_raw, q_raw = parts[0], parts[1]

        try:
            quantita = int(q_raw)
        except ValueError:
            errors.append({"line": idx, "message": "Quantità non numerica"})
            continue

        somma += quantita
        codice_norm = _normalize_codice(codice_raw)

        parsed.append({
            "line": idx,
            "codice_raw": codice_raw,
            "codice": codice_norm,
            "quantita": quantita,
        })

    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    if somma <= 0:
        return Response(
            {"errors": [{"line": 0, "message": "Somma delle quantità minore o uguale a zero"}]},
            status=status.HTTP_400_BAD_REQUEST
        )

    # ---- 3) Validazioni “di dominio”: articolo esiste + concesso ----
    # (A) Preparo set codici richiesti (skip quantita==0 come fai tu)
    richiesti = {p["codice"] for p in parsed if p["quantita"] != 0}

    # (B) Carico tutti gli articoli in 1 query
    articoli_map = {a.nome: a for a in Articolo.objects.filter(nome__in=richiesti)}

    # (C) articoli concessi al proprietario del cliente
    concessi_ids = set(
        ArticoliConcessi.objects
        .filter(proprietario=cliente.proprietario)
        .values_list("articolo", flat=True)
    )

    # (D) Check “esiste” e “concesso”
    for p in parsed:
        if p["quantita"] == 0:
            continue

        articolo = articoli_map.get(p["codice"])
        if articolo is None:
            errors.append({"line": p["line"], "message": f"Articolo errato: {p['codice']}."})
            continue

        if articolo.pk not in concessi_ids:
            errors.append({
                "line": p["line"],
                "message": f"Articolo {p['codice']} non concesso a {cliente.nome} {cliente.via}."
            })

    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    # ---- 4) Creazione bolla + righe in transazione ----
    try:
        with transaction.atomic():
            bolla = Bolla.objects.create(
                cliente=cliente,
                tipo_documento=cliente.tipo_documento_predefinito,
            )

            # Ottimizzazione lotto:
            # qui mantengo la tua logica "prendi ultimo carico per articolo"
            # Se vuoi super-ottimizzare dopo, si può fare batch, ma per 30 prodotti va benissimo.
            for p in parsed:
                if p["quantita"] == 0:
                    continue

                articolo = articoli_map[p["codice"]]

                ultimo_carico = (
                    RigaCarico.objects
                    .filter(articolo=articolo)
                    .order_by("-carico__data")
                    .first()
                )

                RigaBolla.objects.create(
                    bolla=bolla,
                    articolo=articolo,
                    quantita=p["quantita"],
                    lotto=ultimo_carico.lotto if ultimo_carico else "---",
                )

        return Response({"bolla_id": bolla.pk}, status=status.HTTP_201_CREATED)

    except Exception as e:
        # qui NON stampo solo: torno un errore API utile
        return Response(
            {"detail": f"Errore durante la creazione della bolla veloce: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )