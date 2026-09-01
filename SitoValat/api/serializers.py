# api/serializers.py
from rest_framework import serializers
from bolle.models import Articolo, Bolla, Cliente, Concessionario, RigaBolla, Zona


class RigaBollaSerializer(serializers.ModelSerializer):
    codice = serializers.CharField(source="articolo.nome", read_only=True)
    descrizione = serializers.CharField(source="articolo.descrizione", read_only=True)

    class Meta:
        model = RigaBolla
        fields = ["id", "codice", "descrizione", "quantita", "lotto"]


class BollaListSerializer(serializers.ModelSerializer):
    cliente_nome = serializers.CharField(source="cliente.nome", read_only=True)
    cliente_via = serializers.CharField(source="cliente.via", read_only=True)
    cliente_codice = serializers.CharField(source="cliente.codice", read_only=True)
    tipo_documento_nome = serializers.CharField(source="tipo_documento.nome", read_only=True)

    class Meta:
        model = Bolla
        fields = ["id", "numero", "data", "cliente_nome", "cliente_via", "cliente_codice", "tipo_documento_nome"]


class BollaDetailSerializer(serializers.ModelSerializer):
    cliente_nome = serializers.CharField(source="cliente.nome", read_only=True)
    tipo_documento_nome = serializers.CharField(source="tipo_documento.nome", read_only=True)
    righe = RigaBollaSerializer(many=True, read_only=True)

    class Meta:
        model = Bolla
        fields = ["id", "numero", "data", "cliente_nome", "tipo_documento_nome", "righe"]

class CustomerMiniSerializer(serializers.ModelSerializer):
    zona = serializers.SerializerMethodField()
    concessionario = serializers.SerializerMethodField()

    def get_zona(self, obj):
        if not obj.zona_id:
            return None
        return {"id": obj.zona_id, "nome": obj.zona.nome}

    def get_concessionario(self, obj):
        return {"id": obj.concessionario_id, "nome": obj.concessionario.nome}

    class Meta:
        model = Cliente
        fields = ("id", "nome", "via", "codice", "citta", "zona", "concessionario")


class ArticoloMiniSerializer(serializers.ModelSerializer):
    codice = serializers.CharField(source="nome", read_only=True)

    class Meta:
        model = Articolo
        fields = ("id", "codice", "descrizione")
