# api/serializers.py
from rest_framework import serializers
from bolle.models import Bolla, RigaBolla


class RigaBollaSerializer(serializers.ModelSerializer):
    codice = serializers.CharField(source="articolo.nome", read_only=True)
    descrizione = serializers.CharField(source="articolo.descrizione", read_only=True)

    class Meta:
        model = RigaBolla
        fields = ["id", "codice", "descrizione", "quantita", "lotto"]


class BollaListSerializer(serializers.ModelSerializer):
    cliente_nome = serializers.CharField(source="cliente.nome", read_only=True)
    tipo_documento_nome = serializers.CharField(source="tipo_documento.nome", read_only=True)

    class Meta:
        model = Bolla
        fields = ["id", "numero", "data", "cliente_nome", "tipo_documento_nome"]


class BollaDetailSerializer(serializers.ModelSerializer):
    cliente_nome = serializers.CharField(source="cliente.nome", read_only=True)
    tipo_documento_nome = serializers.CharField(source="tipo_documento.nome", read_only=True)
    righe = RigaBollaSerializer(many=True, read_only=True)

    class Meta:
        model = Bolla
        fields = ["id", "numero", "data", "cliente_nome", "tipo_documento_nome", "righe"]