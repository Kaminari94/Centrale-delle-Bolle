from datetime import datetime, time, timedelta
import hashlib
import json
import uuid
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.db import IntegrityError, transaction
from django.db.models import Case, IntegerField, Max, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from bolle.models import (
    Articolo, Bolla, Carico, Cliente, Reso, RigaCarico, RigaReso, TipoDocCounter,
    TipoDocumento, Zona, is_quickbolla_offline_tipo,
)
from .serializers import ArticoloMiniSerializer, BollaListSerializer, BollaDetailSerializer, CustomerMiniSerializer
from .receipts.renderer import render_ddt
from django.core.exceptions import ValidationError
from rest_framework import status
from bolle.services.codes import normalize_article_code
from bolle.services.documents import create_bolla_with_lines, replace_bolla_lines
from bolle.services.rows import (
    allowed_article_ids, create_bolla_row, default_lot_for_article, parse_quantity,
    validate_article_lines,
)

@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"ok": True})

def _apply_customer_scope(qs, user):
    # Per un accesso amministrativo globale, ripristinare questo bypass:
    # if getattr(user, "is_superuser", False):
    #     return qs

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
    - altrimenti: nessun accesso
    """
    # Per un accesso amministrativo globale, ripristinare questo bypass:
    # if getattr(user, "is_superuser", False):
    #     return qs

    user_conc = getattr(user, "concessionario", None)
    user_zona = getattr(user, "zona", None)
    if user_conc is not None:
        return qs.filter(cliente__concessionario=user_conc)
    elif user_zona is not None:
        return qs.filter(cliente__zona=user_zona)

    return qs.none()


def _parse_limit(request, default=20, maximum=100):
    try:
        limit = int(request.query_params.get("limit", default))
    except (TypeError, ValueError):
        return default
    return min(max(limit, 1), maximum)


def _allowed_articles_for_customer(cliente):
    return Articolo.objects.filter(pk__in=allowed_article_ids(cliente))


def _visible_articles_for_user(user):
    clienti_visibili = _apply_customer_scope(Cliente.objects.all(), user)
    return Articolo.objects.filter(
        concessioni__proprietario__clienti__in=clienti_visibili
    ).distinct()


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

    if bolla.receipt_text:
        return Response({
            "width": bolla.receipt_width or 32,
            "text": bolla.receipt_text,
            "renderer": bolla.receipt_renderer or "ddt-receipt-v1",
            "source": "stored",
        })
    text = render_ddt(bolla)
    return Response({"width": 32, "text": text, "renderer": "ddt-receipt-v1", "source": "rendered"})

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def customers_list(request):
    """
    GET /api/customers/?q=...
    Ritorna lista clienti per dropdown (id, nome), scoped per user.
    """
    qs = Cliente.objects.select_related("zona", "concessionario")

    # (opzionale) escludi NTV come fai tu
    qs = qs.exclude(tipo_documento_predefinito__nome="NTV")
    qs = qs.exclude(tipo_documento_predefinito__nome="RF")
    qs = qs.exclude(ignorare=True)
    qs = _apply_customer_scope(qs, request.user)

    q = request.query_params.get("q")
    if q:
        qs = qs.filter(Q(nome__icontains=q) | Q(codice__icontains=q))

    qs = qs.order_by("nome")
    limit = _parse_limit(request)

    data = CustomerMiniSerializer(qs[:limit], many=True).data
    return Response({"count": len(data), "results": data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def articles_list(request):
    """GET /api/articles/?q=&customer_id=&limit=20."""
    qs = _visible_articles_for_user(request.user)
    customer_id = request.query_params.get("customer_id")

    if customer_id is not None:
        try:
            customer_id = int(customer_id)
        except (TypeError, ValueError):
            return Response({"detail": "customer_id non valido"}, status=status.HTTP_400_BAD_REQUEST)
        cliente = get_object_or_404(
            _apply_customer_scope(Cliente.objects.all(), request.user),
            pk=customer_id,
        )
        qs = _allowed_articles_for_customer(cliente)

    q = (request.query_params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(nome__icontains=q) | Q(descrizione__icontains=q)).annotate(
            search_priority=Case(
                When(nome__iexact=q, then=Value(0)),
                When(nome__istartswith=q, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        ).order_by("search_priority", "nome")
    else:
        qs = qs.order_by("nome")

    limit = _parse_limit(request)
    data = ArticoloMiniSerializer(qs[:limit], many=True).data
    return Response({"count": len(data), "results": data})


def _quickbolla_next_number(tipo_documento, anno):
    """The next number visible to Django, without reserving or allocating it."""
    counter_value = (
        TipoDocCounter.objects.filter(tipo=tipo_documento, anno=anno)
        .values_list("ultimo_numero", flat=True)
        .first()
        or 0
    )
    document_value = (
        Bolla.objects.filter(tipo_documento=tipo_documento, anno_emissione=anno)
        .aggregate(maximum=Max("numero"))["maximum"]
        or 0
    )
    return max(counter_value, document_value) + 1


def _quickbolla_payload_hash(payload):
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _sync_error(detail, *, code="invalid_payload", status_code=status.HTTP_400_BAD_REQUEST):
    return Response({"code": code, "detail": detail}, status=status_code)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def quickbolla_catalog(request):
    """Offline catalog and print data for the CLS/NT Va.Lat delivery run."""
    year_raw = request.query_params.get("year")
    try:
        anno = int(year_raw) if year_raw else timezone.localdate().year
    except (TypeError, ValueError):
        return _sync_error("Parametro 'year' non valido.")
    if anno < 2000 or anno > 9999:
        return _sync_error("Parametro 'year' non valido.")

    tipi = [
        tipo for tipo in TipoDocumento.objects.select_related("concessionario", "concessionario__cons_conto")
        .filter(nome__in=["CLS", "NT"]).order_by("nome", "pk")
        if is_quickbolla_offline_tipo(tipo)
    ]
    if not tipi:
        return Response({"year": anno, "types": [], "customers": [], "articles": []})

    tipo_ids = [tipo.pk for tipo in tipi]
    clienti = (
        _apply_customer_scope(Cliente.objects.select_related("proprietario", "tipo_documento_predefinito"), request.user)
        .filter(tipo_documento_predefinito_id__in=tipo_ids, ignorare=False)
        .order_by("nome", "pk")
    )

    article_ids = set()
    customer_data = []
    for cliente in clienti:
        allowed_ids = allowed_article_ids(cliente)
        article_ids.update(allowed_ids)
        proprietario = cliente.proprietario
        customer_data.append({
            "id": cliente.pk,
            "nome": cliente.nome,
            "via": cliente.via,
            "cap": cliente.cap,
            "citta": cliente.citta,
            "piva": cliente.piva,
            "codice": cliente.codice,
            "tipo_documento_id": cliente.tipo_documento_predefinito_id,
            "proprietario": {
                "nome": proprietario.nome if proprietario else "",
                "codice": proprietario.codice if proprietario else "",
            },
            "_allowed_article_ids": sorted(allowed_ids),
        })

    articles = Articolo.objects.filter(pk__in=article_ids).order_by("nome")
    article_data = [
        {
            "id": article.pk,
            "codice": article.nome,
            "descrizione": article.descrizione or "",
            "ultimo_lotto": default_lot_for_article(article),
        }
        for article in articles
    ]
    article_codes_by_id = {article["id"]: article["codice"] for article in article_data}
    for customer in customer_data:
        customer["articoli_consentiti"] = [
            article_codes_by_id[article_id]
            for article_id in customer.pop("_allowed_article_ids")
            if article_id in article_codes_by_id
        ]
    tipo_data = [
        {
            "id": tipo.pk,
            "nome": tipo.nome,
            "descrizione": tipo.descrizione or "",
            "next_number": _quickbolla_next_number(tipo, anno),
        }
        for tipo in tipi
    ]
    concessionario = tipi[0].concessionario
    cons_conto = concessionario.cons_conto
    return Response({
        "schema_version": 1,
        "generated_at": timezone.now().isoformat(),
        "year": anno,
        "printer": {"renderer": "ddt-receipt-v1", "width": 32, "encoding": "UTF-8"},
        "concessionario": {
            "id": concessionario.pk,
            "nome": concessionario.nome,
            "indirizzo": concessionario.indirizzo,
            "partita_iva": concessionario.partita_iva,
            "telefono": concessionario.telefono or "",
            "consegna_per_conto_di": (
                {"nome": cons_conto.nome, "indirizzo": cons_conto.indirizzo} if cons_conto else None
            ),
        },
        "types": tipo_data,
        "customers": customer_data,
        "articles": article_data,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def quickbolla_sync(request):
    """Persist a document already numbered and printed by QuickBolla offline."""
    try:
        emission_id = uuid.UUID(str(request.data.get("emission_id")))
    except (TypeError, ValueError, AttributeError):
        return _sync_error("Campo 'emission_id' UUID mancante o non valido.")

    try:
        customer_id = int(request.data.get("customer_id"))
        tipo_documento_id = int(request.data.get("tipo_documento_id"))
        numero = int(request.data.get("numero"))
        anno_emissione = int(request.data.get("anno_emissione"))
    except (TypeError, ValueError):
        return _sync_error("customer_id, tipo_documento_id, numero e anno_emissione devono essere interi.")
    if numero <= 0 or anno_emissione < 2000 or anno_emissione > 9999:
        return _sync_error("Numero o anno_emissione non valido.")

    data_emissione = parse_datetime(request.data.get("data_emissione") or "")
    if data_emissione is None or timezone.is_naive(data_emissione):
        return _sync_error("data_emissione deve essere ISO-8601 completa di fuso orario.")
    data_emissione = timezone.localtime(data_emissione)
    if data_emissione.year != anno_emissione:
        return _sync_error("anno_emissione deve coincidere con l'anno di data_emissione.")

    cliente = get_object_or_404(
        _apply_customer_scope(Cliente.objects.select_related("tipo_documento_predefinito"), request.user),
        pk=customer_id,
    )
    tipo_documento = cliente.tipo_documento_predefinito
    if not tipo_documento or tipo_documento.pk != tipo_documento_id:
        return _sync_error("Il tipo documento non coincide con quello configurato sul cliente.")
    if not is_quickbolla_offline_tipo(tipo_documento):
        return _sync_error("La sincronizzazione offline e consentita solo per CLS e NT di Va.Lat.", code="unsupported_type", status_code=status.HTTP_403_FORBIDDEN)

    righe_input = request.data.get("righe")
    if not isinstance(righe_input, list) or not righe_input:
        return _sync_error("'righe' deve essere una lista non vuota.")
    parsed = []
    for index, row in enumerate(righe_input, start=1):
        if not isinstance(row, dict):
            return _sync_error(f"Riga {index} non valida.")
        try:
            quantita = int(row.get("quantita"))
        except (TypeError, ValueError):
            return _sync_error(f"Quantita non valida alla riga {index}.")
        lotto = row.get("lotto")
        if quantita <= 0 or not isinstance(lotto, str) or not lotto.strip():
            return _sync_error(f"Quantita o lotto non valido alla riga {index}.")
        codice = normalize_article_code(str(row.get("codice") or ""))
        if not codice:
            return _sync_error(f"Codice mancante alla riga {index}.")
        parsed.append({"line": index, "codice": codice, "quantita": quantita, "lotto": lotto.strip()})

    articles_by_code, errors = validate_article_lines(cliente, parsed, include_customer_address=True)
    if errors:
        return Response({"code": "invalid_lines", "errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    printed_receipt = request.data.get("printed_receipt")
    if not isinstance(printed_receipt, dict):
        return _sync_error("printed_receipt mancante o non valido.")
    receipt_text = printed_receipt.get("text")
    renderer = printed_receipt.get("renderer")
    width = printed_receipt.get("width")
    if not isinstance(receipt_text, str) or not receipt_text or not isinstance(renderer, str):
        return _sync_error("printed_receipt deve contenere text e renderer.")
    try:
        width = int(width)
    except (TypeError, ValueError):
        return _sync_error("printed_receipt.width non valido.")
    if width <= 0:
        return _sync_error("printed_receipt.width non valido.")

    payload_hash = _quickbolla_payload_hash({
        "emission_id": str(emission_id),
        "customer_id": customer_id,
        "tipo_documento_id": tipo_documento_id,
        "numero": numero,
        "anno_emissione": anno_emissione,
        "data_emissione": data_emissione.isoformat(),
        "righe": [{"codice": row["codice"], "quantita": row["quantita"], "lotto": row["lotto"]} for row in parsed],
        "printed_receipt": {"renderer": renderer, "width": width, "text": receipt_text},
    })

    try:
        with transaction.atomic():
            existing = Bolla.objects.filter(emission_id=emission_id).first()
            if existing:
                if existing.sync_payload_hash == payload_hash:
                    return Response({
                        "status": "already_synced", "bolla_id": existing.pk,
                        "emission_id": str(emission_id), "numero": existing.numero,
                        "tipo_documento_id": existing.tipo_documento_id,
                    })
                return _sync_error(
                    "L'emission_id esiste gia con contenuto diverso.",
                    code="emission_content_conflict", status_code=status.HTTP_409_CONFLICT,
                )

            conflicting = Bolla.objects.filter(
                tipo_documento=tipo_documento, anno_emissione=anno_emissione, numero=numero
            ).first()
            if conflicting:
                return Response({
                    "code": "number_conflict", "tipo_documento_id": tipo_documento.pk,
                    "anno_emissione": anno_emissione, "numero": numero,
                    "existing_bolla_id": conflicting.pk,
                }, status=status.HTTP_409_CONFLICT)

            bolla = Bolla(
                cliente=cliente, tipo_documento=tipo_documento, data=data_emissione,
                anno_emissione=anno_emissione, numero=numero, emission_id=emission_id,
                sync_payload_hash=payload_hash, receipt_text=receipt_text,
                receipt_renderer=renderer, receipt_width=width,
            )
            bolla.save(skip_auto_number=True)
            for row in parsed:
                create_bolla_row(bolla, articles_by_code[row["codice"]], row["quantita"], lotto=row["lotto"])

            counter, _ = TipoDocCounter.objects.select_for_update().get_or_create(
                tipo=tipo_documento, anno=anno_emissione, defaults={"ultimo_numero": 0}
            )
            if counter.ultimo_numero < numero:
                counter.ultimo_numero = numero
                counter.save(update_fields=["ultimo_numero"])
                tipo_documento.ultimo_numero = max(tipo_documento.ultimo_numero, numero)
                tipo_documento.save(update_fields=["ultimo_numero"])
    except IntegrityError:
        conflicting = Bolla.objects.filter(
            tipo_documento=tipo_documento, anno_emissione=anno_emissione, numero=numero
        ).first()
        if conflicting:
            return Response({
                "code": "number_conflict", "tipo_documento_id": tipo_documento.pk,
                "anno_emissione": anno_emissione, "numero": numero,
                "existing_bolla_id": conflicting.pk,
            }, status=status.HTTP_409_CONFLICT)
        raise

    return Response({
        "status": "created", "bolla_id": bolla.pk, "emission_id": str(emission_id),
        "numero": bolla.numero, "tipo_documento_id": bolla.tipo_documento_id,
    }, status=status.HTTP_201_CREATED)


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
    cliente = get_object_or_404(
        _apply_customer_scope(Cliente.objects.all(), request.user),
        pk=customer_id,
    )

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
            quantita = parse_quantity(q_raw)
        except ValueError:
            errors.append({"line": idx, "message": "Quantità non numerica"})
            continue

        somma += quantita
        codice_norm = normalize_article_code(codice_raw)

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

    articoli_map, article_errors = validate_article_lines(
        cliente,
        parsed,
        include_customer_address=True,
    )
    errors.extend(article_errors)

    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    # ---- 4) Creazione bolla + righe in transazione ----
    try:
        bolla = create_bolla_with_lines(cliente, parsed, articoli_map)

        return Response({"bolla_id": bolla.pk}, status=status.HTTP_201_CREATED)

    except Exception as e:
        # qui NON stampo solo: torno un errore API utile
        return Response(
            {"detail": f"Errore durante la creazione della bolla veloce: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def movim_quick_create(request):
    """
    POST /api/movim/
    Body JSON:
      {
        "type": "Carico" (oppure "Reso"),
        "numero": "S1V000...", //Solo carico
        "raw_lines": "127 5 160426\n128 2 160426\n..." (oppure caso Reso: "127 5\n128 2\n...")
      }

    Success:
      201 { "type": "carico"/"reso", "mov_id": 123 }

    Validation error:
      400 { "errors": [ { "line": 2, "message": "..." }, ... ] }
    """
    mov_type = request.data.get("type") # Rinominato per evitare conflitto con la keyword 'type'
    numero = request.data.get("numero") if mov_type == "Carico" else None
    raw_lines = request.data.get("raw_lines", "")
    user = request.user

    if mov_type is None:
        return Response({"detail": "Campo 'type' mancante."}, status=status.HTTP_400_BAD_REQUEST)

    if mov_type not in ["Carico", "Reso"]:
        return Response({"detail": "Campo 'type' non valido. Deve essere 'Carico' o 'Reso'."}, status=status.HTTP_400_BAD_REQUEST)

    if mov_type == "Carico" and not numero:
        return Response({"detail": "Campo 'numero' mancante per il tipo 'Carico'."}, status=status.HTTP_400_BAD_REQUEST)

    if not isinstance(raw_lines, str) or not raw_lines.strip():
        return Response({"detail": "Campo 'raw_lines' vuoto o non valido."}, status=status.HTTP_400_BAD_REQUEST)

    # ---- 1) Parsing righe + controlli di base  ----
    lines = [ln.strip() for ln in raw_lines.strip().split("\n") if ln.strip()]
    errors = []
    parsed = []  # lista di dict: {line, codice_raw, codice_norm, quantita, lotto (opzionale)}

    somma = 0
    for idx, ln in enumerate(lines, start=1):
        parts = ln.split()
        codice_raw = None
        q_raw = None
        lotto = None # Inizializza lotto a None

        if mov_type == "Carico":
            if len(parts) != 3:
                errors.append({"line": idx, "message": "Formato non valido per Carico. Usa: CODICE QUANTITÀ LOTTO"})
                continue
            codice_raw, q_raw, lotto = parts[0], parts[1], parts[2]
        elif mov_type == "Reso":
            if len(parts) != 2:
                errors.append({"line": idx, "message": "Formato non valido per Reso. Usa: CODICE QUANTITÀ"})
                continue
            codice_raw, q_raw = parts[0], parts[1]

        try:
            quantita = parse_quantity(q_raw)
            if quantita <= 0:
                errors.append({"line": idx, "message": "Quantità deve essere maggiore di zero."})
                continue
        except (ValueError, TypeError):
            errors.append({"line": idx, "message": "Quantità non numerica o non valida."})
            continue

        somma += quantita
        codice_norm = normalize_article_code(codice_raw)

        item_data = {
            "line": idx,
            "codice_raw": codice_raw,
            "codice": codice_norm,
            "quantita": quantita,
        }
        if mov_type == "Carico":
            item_data["lotto"] = lotto # Aggiungi lotto solo se è un Carico
        parsed.append(item_data)


    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    if somma <= 0:
        return Response(
            {"errors": [{"line": 0, "message": "La somma delle quantità deve essere maggiore di zero."}]},
            status=status.HTTP_400_BAD_REQUEST
        )

    # ---- 3) Validazioni “di dominio”: articolo esiste ----
    # (A) Preparo set codici richiesti (skip quantita==0 già gestito sopra con quantita > 0)
    richiesti = {p["codice"] for p in parsed}

    # (B) Carico tutti gli articoli in 1 query
    articoli_map = {a.nome: a for a in Articolo.objects.filter(nome__in=richiesti)}

    # (C) Check “esiste”
    for p in parsed:
        articolo = articoli_map.get(p["codice"])
        if articolo is None:
            errors.append({"line": p["line"], "message": f"Articolo non trovato: {p['codice']}."})
            continue

    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)


    # Recupera concessionario e zona dell'utente
    user_conc = getattr(user, "concessionario", None)
    user_zona = getattr(user, "zona", None) if not user_conc else Zona.objects.filter(concessionario=user_conc).first()
    # print(user_zona)

    # Determina la zona e il fornitore (se Carico)
    zona_movimento = None
    fornitore_carico = None

    if user_zona:
        zona_movimento = user_zona
        fornitore_carico = user_zona.concessionario.cons_conto
        # print(zona_movimento, fornitore_carico)
    elif user_conc:
        qs = Zona.objects.filter(concessionario=user_conc)
        if hasattr(user_conc, 'zona') and user_conc.zona:
            zona_movimento = qs.first()
        if hasattr(user_conc, 'cons_conto') and user_conc.cons_conto:
            fornitore_carico = user_conc.cons_conto
    else:
        # Gestire il caso in cui l'utente non ha né zona né concessionario
        return Response(
            {"detail": "L'utente non è associato a una zona o un concessionario valido per effettuare movimenti."},
            status=status.HTTP_403_FORBIDDEN
        )

    if not zona_movimento:
         return Response(
            {"detail": "Impossibile determinare la zona per il movimento dall'utente corrente."},
            status=status.HTTP_400_BAD_REQUEST
        )

    # ---- 4) Creazione movimento + righe in transazione ----
    try:
        with transaction.atomic():
            if mov_type == "Carico":
                if not fornitore_carico:
                     return Response(
                        {"detail": "Impossibile determinare il fornitore per il Carico dall'utente corrente."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                movimento = Carico.objects.create(
                    data=datetime.now(),
                    zona=zona_movimento,
                    fornitore=fornitore_carico,
                    numero=numero,
                )
            else: # Reso
                movimento = Reso.objects.create(
                    data=datetime.now(),
                    zona=zona_movimento,
                )

            for p in parsed:
                # Quantità <= 0 è già stata gestita nel parsing, quindi qui non serve il controllo
                articolo_obj = articoli_map[p["codice"]] # Recupera l'oggetto Articolo

                if mov_type == "Carico":
                    RigaCarico.objects.create(
                        carico=movimento,
                        articolo=articolo_obj,
                        quantita=p["quantita"],
                        lotto=p.get("lotto", "---"), # Usa p["lotto"] con fallback
                    )
                else: # Reso
                    RigaReso.objects.create(
                        reso=movimento,
                        articolo=articolo_obj,
                        quantita=p["quantita"],
                    )

            if mov_type == "Carico":
                return Response({"type": "carico", "mov_id": movimento.pk}, status=status.HTTP_201_CREATED)
            else:
                return Response({"type": "reso", "mov_id": movimento.pk}, status=status.HTTP_201_CREATED)

    except Exception as e:
        # È buona pratica loggare l'errore completo per debug
        # import logging
        # logging.exception("Errore durante la creazione del movimento veloce")
        return Response(
            {"detail": f"Errore interno durante la creazione del movimento: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(["DELETE"])  # <-- 1. Cambiato a DELETE
@permission_classes([IsAuthenticated])
@transaction.atomic
def delete_view(request, pk: int):
    """
    DELETE /api/bolle/<id>/

    Success:
      204 No Content

    Validation error:
      400 { "detail": "Non puoi eliminare una bolla se non è l'ultima creata." }

    Not Found:
      404 { "detail": "Not found." }
    """

    qs = _apply_user_scope(Bolla.objects.all(), request.user)
    bolla = get_object_or_404(qs, pk=pk)
    tipo = bolla.tipo_documento
    anno = bolla.data.year

    try:
        counter = (TipoDocCounter.objects
                   .select_for_update()
                   .get(tipo=tipo, anno=anno))

        ultima_bolla = (Bolla.objects
                        .filter(tipo_documento=tipo, data__year=anno)
                        .order_by('-numero')
                        .first())

        if bolla != ultima_bolla:
            return Response({"detail": str("Puoi eliminare solo l'ultima bolla di quell'anno per quel tipo documento.")}, status=status.HTTP_400_BAD_REQUEST)

        if not is_quickbolla_offline_tipo(tipo):
            counter.ultimo_numero -= 1
            counter.save(update_fields=["ultimo_numero"])

            # cache (optional)
            tipo_doc = TipoDocumento.objects.select_for_update().get(pk=tipo.pk)
            tipo_doc.ultimo_numero = counter.ultimo_numero
            tipo_doc.save(update_fields=["ultimo_numero"])

        bolla.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    except ValidationError as e:
        # 3. Gestione di un'eccezione specifica per la regola di business
        # 2. Codice di errore corretto (400)
        return Response({"detail": str(e.message)}, status=status.HTTP_400_BAD_REQUEST)

    except Exception as e:
        # Gestione per tutti gli altri errori imprevisti
        return Response(
            {"detail": "Si è verificato un errore interno durante l'eliminazione."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def modifica_righe(request, pk: int):
    """
    POST /api/bolle/<id>/modifica/
    Body JSON:
      {
        "raw_lines": "127 5\n128 2\n..."
      }
    
    Sostituisce COMPLETAMENTE le righe della bolla con quelle fornite.
    Mantiene i lotti esistenti per gli articoli già presenti.
    """
    raw_lines = request.data.get("raw_lines", "")
    
    if not isinstance(raw_lines, str):
        return Response({"detail": "raw_lines deve essere una stringa"}, status=status.HTTP_400_BAD_REQUEST)

    # 1. Recupero la bolla esistente (con scope utente)
    qs = _apply_user_scope(Bolla.objects.all(), request.user)
    bolla = get_object_or_404(qs, pk=pk)
    cliente = bolla.cliente # Serve per i controlli sui permessi articoli

    # 2. Parsing righe (identico a quick_create)
    lines = [ln.strip() for ln in raw_lines.strip().split("\n") if ln.strip()]
    errors = []
    parsed = [] 

    somma = 0
    for idx, ln in enumerate(lines, start=1):
        parts = ln.split()
        if len(parts) != 2:
            errors.append({"line": idx, "message": "Formato non valido. Usa: CODICE QUANTITÀ"})
            continue

        codice_raw, q_raw = parts[0], parts[1]

        try:
            quantita = parse_quantity(q_raw)
        except ValueError:
            errors.append({"line": idx, "message": "Quantità non numerica"})
            continue

        somma += quantita
        codice_norm = normalize_article_code(codice_raw)

        parsed.append({
            "line": idx,
            "codice_raw": codice_raw,
            "codice": codice_norm,
            "quantita": quantita,
        })

    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    if somma <= 0 and len(parsed) > 0:
         # Nota: se parsed è vuoto, vuol dire che l'utente vuole cancellare tutto. 
         # Se non è vuoto ma somma è 0, è un errore.
        return Response(
            {"errors": [{"line": 0, "message": "Somma delle quantità minore o uguale a zero"}]},
            status=status.HTTP_400_BAD_REQUEST
        )

    articoli_map, article_errors = validate_article_lines(cliente, parsed)
    errors.extend(article_errors)

    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    # 5. Sostituzione Righe (Atomica)
    try:
        replace_bolla_lines(bolla, parsed, articoli_map)

        return Response({"type": "updated", "bolla_id": bolla.pk, "rows_count": len(parsed)}, status=status.HTTP_200_OK)

    except Exception as e:
        return Response(
            {"detail": f"Errore durante l'aggiornamento delle righe: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
