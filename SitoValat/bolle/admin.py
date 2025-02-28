from django.contrib import admin
from .models import *

admin.site.register(ArticoliConcessi)
admin.site.register(PrezziPersonalizzati)

@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ordine')

@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('codice','nome', 'indirizzo', 'proprietario', 'zona')
    list_filter = ('zona', 'concessionario')
    search_fields = ('nome',)

@admin.register(Proprietario)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('codice', 'nome', 'indirizzo')

@admin.register(Concessionario)
class ConcessionarioAdmin(admin.ModelAdmin):
    list_display = ('nome', 'telefono', 'partita_iva')
    list_filter = ('user',)
    search_fields = ('nome',)

@admin.register(TipoDocumento)
class TipoDocumentoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'descrizione', 'ultimo_numero')

@admin.register(Fornitore)
class FornitoreAdmin(admin.ModelAdmin):
    list_display = ('nome', 'telefono', 'partita_iva')

@admin.register(Articolo)
class ArticoloAdmin(admin.ModelAdmin):
    list_display = ('nome', 'descrizione', 'categoria', 'costo', 'prezzo', 'iva')
    list_filter = ('categoria',)
    search_fields = ('nome','descrizione',)

@admin.register(Bolla)
class BollaAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'tipo_documento', 'numero', 'data', 'note')
    list_filter = ('data', 'cliente', 'tipo_documento')
    search_fields = ('nome', 'cliente')

@admin.register(RigaBolla)
class RigaBollaAdmin(admin.ModelAdmin):
    list_display = ('bolla', 'articolo', 'quantita')
    list_filter = ('bolla',)

@admin.register(Zona)
class ZonaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'concessionario', 'user')
    list_filter = ('concessionario',)

@admin.register(Carico)
class CaricoAdmin(admin.ModelAdmin):
    list_display = ('zona', 'data', 'numero', 'fornitore', 'note')
    list_filter = ('data', 'zona', 'fornitore')
    search_fields = ('numero', 'zona')

@admin.register(RigaCarico)
class RigaCaricoAdmin(admin.ModelAdmin):
    list_display = ('carico', 'articolo', 'lotto')
    list_filter = ('carico','articolo')

@admin.register(Reso)
class ResoAdmin(admin.ModelAdmin):
    list_display = ('zona', 'data', 'note')
    list_filter = ('data', 'zona')

@admin.register(RigaReso)
class RigaResoAdmin(admin.ModelAdmin):
    list_display = ('reso', 'articolo', 'quantita')
    search_fields = ('reso','articolo')

@admin.register(TipoFattura)
class TipoFatturaAdmin(admin.ModelAdmin):
    list_display = ('tipo', 'descrizione', 'ultimo_numero', 'concessionario')

@admin.register(Fattura)
class FatturaAdmin(admin.ModelAdmin):
    list_display = ('concessionario', 'cliente', 'tipo_fattura', 'numero', 'data', 'note')
    list_filter = ('data', 'concessionario', 'cliente', 'tipo_fattura')
    search_fields = ('concessionario', 'cliente')

@admin.register(RigaFattura)
class RigaFatturaAdmin(admin.ModelAdmin):
    list_display = ('fattura', 'articolo', 'quantita')
    list_filter = ('fattura',)


@admin.register(SchedaTV)
class SchedaTVAdmin(admin.ModelAdmin):
    list_display = ('data', 'numero', 'cliente')
    list_filter = ('data', 'cliente')
    search_fields = ('numero',)

@admin.register(RigaSchedaTV)
class RigaSchedaTVAdmin(admin.ModelAdmin):
    list_display = ('scheda', 'giorno', 'articolo', 'quantita')
    list_filter = ('scheda','articolo')
