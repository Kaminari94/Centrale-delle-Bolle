from django.shortcuts import get_object_or_404
from datetime import datetime, time, timedelta
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from django.db.models import Q

from bolle.models import Bolla
from .serializers import BollaListSerializer, BollaDetailSerializer
from .receipts.renderer import render_ddt


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"ok": True})


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