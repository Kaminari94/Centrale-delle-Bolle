from django.db import models
from django.db import transaction
from django.contrib.auth.models import User
from datetime import datetime
from django.core.exceptions import ValidationError
from decimal import Decimal, ROUND_HALF_UP
from django.utils.timezone import make_aware
from django.utils import timezone



def get_aware_datetime():
    return make_aware(datetime.now())

class Articolo(models.Model):
    IVA_SCELTE = [
        (4, '4%'),
        (10, '10%'),
        (22, '22%'),
    ]

    nome = models.CharField(max_length=255)  # Nome dell'articolo
    descrizione = models.TextField(blank=True, null=True)  # Opzionale
    categoria = models.ForeignKey('Categoria', on_delete=models.CASCADE, related_name='articoli')  # Relazione con Categoria
    iva = models.PositiveSmallIntegerField(choices=IVA_SCELTE, default=4)
    prezzo = models.DecimalField(max_digits=10, decimal_places=3, default=0.0)  # Prezzo normal trade
    prezzo_tr = models.DecimalField(max_digits=10, decimal_places=3, default=0.0)  # Prezzo transfert
    costo = models.DecimalField(max_digits=10, decimal_places=3, default=0.0)  # Costo acquisto

    def __str__(self):
        return f"{self.nome} - {self.descrizione or 'N/A'}"

    def clean(self):
        """Validazione personalizzata per i prezzi e i costi."""
        if self.prezzo < 0 or self.prezzo_tr < 0 or self.costo < 0:
            raise ValidationError("I prezzi e i costi non possono essere negativi.")

    @property
    def margine_lordo(self):
        """Calcola il margine lordo come prezzo - costo."""
        return self.prezzo - self.costo

    @property
    def prezzo_ivato(self):
        """Calcola il prezzo con IVA inclusa."""
        return self.prezzo * Decimal(1 + self.iva / 100)

    @property
    def costo_ivato(self):
        """Calcola il costo con IVA inclusa."""
        return self.costo * Decimal(1 + self.iva / 100)

    @property
    def prezzo_tr_ivato(self):
        """Calcola il prezzo transfert con IVA inclusa."""
        return self.prezzo_tr * Decimal(1 + self.iva / 100)

class Categoria(models.Model):
    nome = models.CharField(max_length=255, unique=True)  # Nome della categoria (es. Elettronica, Alimentari)
    ordine = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['ordine'] # Ordina automaticamente per ordine

    def __str__(self):
        return self.nome
from django.db import models

class Cliente(models.Model):
    nome = models.CharField(max_length=255)
    concessionario = models.ForeignKey('Concessionario', on_delete=models.CASCADE)  # Concessionario
    indirizzo = models.TextField(null=True, blank=True)
    via = models.CharField(max_length=255, default="")
    cap = models.CharField(max_length=5, default = "00000")
    citta = models.CharField(max_length=60, default = "")
    provincia = models.CharField(max_length=2, default = "SA")
    piva = models.CharField(max_length=20)  # Partita IVA
    codice_fiscale = models.CharField(max_length=16, unique=True, blank=True, null=True)
    cod_dest = models.CharField(max_length=7, default="0000000") # Codice Destinatario SDI
    pec = models.CharField(max_length=50, default="")
    tipo_documento_predefinito = models.ForeignKey(
        'TipoDocumento',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    proprietario = models.ForeignKey('Proprietario', on_delete=models.SET_NULL, null=True, blank=True, related_name='clienti')
    codice = models.CharField(max_length=30, default="0000")
    zona = models.ForeignKey('Zona', on_delete=models.CASCADE, related_name='clienti', default="", null=True, blank=True)
    def __str__(self):
        return f"{self.tipo_documento_predefinito} | {self.nome} - {self.via}"

class RigaBolla(models.Model):
    bolla = models.ForeignKey('Bolla', on_delete=models.CASCADE, related_name='righe')  # Relazione con Bolla
    articolo = models.ForeignKey('Articolo', on_delete=models.CASCADE)  # Relazione con Articolo
    quantita = models.PositiveIntegerField()  # Quantità ordinata
    lotto = models.CharField(
        max_length=20,
        blank=True,
        null=True
    )

    def __str__(self):
        return f"{self.bolla} - {self.quantita} x {self.articolo.nome} | Lotto: {self.lotto}"

class Concessionario(models.Model):
    nome = models.CharField(max_length=255)
    indirizzo = models.TextField()
    via = models.CharField(max_length=255, default="")
    cap = models.CharField(max_length=5, default = "00000")
    citta = models.CharField(max_length=60, default = "")
    provincia = models.CharField(max_length=2, default = "SA")
    header = models.CharField(max_length=20, default = "00000000000000000000")
    codice_fiscale = models.CharField(max_length=16, unique=True, blank=True, null=True)
    partita_iva = models.CharField(max_length=20)
    telefono = models.CharField(max_length=15, blank=True, null=True)
    cons_conto = models.ForeignKey("Fornitore", on_delete=models.SET_NULL, null=True, blank=True, related_name='concessionari')
    cod_dest = models.CharField(max_length=7, default="0000000")  # Codice Destinatario SDI
    pec = models.CharField(max_length=50, default="")
    istituto_finanziario = models.CharField(max_length=200, default="")
    iban = models.CharField(max_length=50, default="")
    logo = models.ImageField(upload_to='logos/', null=True, blank=True)
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='concessionario', null=True, blank=True
    )  # Associa un utente al concessionario
    # related_name='concessionario': Permette di accedere al concessionario di un utente con user.concessionario.

    def __str__(self):
        return self.nome

class TipoDocumento(models.Model):
    nome = models.CharField(max_length=5) # Nome breve (Es: CLS, NOC)
    descrizione = models.CharField(max_length=255, blank=True, null=True)
    ultimo_numero = models.PositiveIntegerField(default=0)
    concessionario = models.ForeignKey('Concessionario', on_delete=models.CASCADE)
    def __str__(self):
        return self.nome

class Bolla(models.Model):
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE)  # Cliente associato
    tipo_documento = models.ForeignKey('TipoDocumento', on_delete=models.CASCADE)  # Tipo di documento
    data = models.DateTimeField(default=get_aware_datetime, blank=True, null=True)  # Data di creazione
    numero = models.PositiveIntegerField()  # Numero incrementale della bolla
    note = models.TextField(blank=True, null=True)  # Eventuali note aggiuntive

    def save(self, *args, **kwargs):
        skip_auto_number = kwargs.pop('skip_auto_number', False)  # Recuperiamo il flag, DA PROVARE
        if not self.id and not skip_auto_number:  # Controlla se la bolla è nuova
            with transaction.atomic():  # Blocca la transazione per garantire unicità
                tipo_doc = self.tipo_documento
                tipo_doc.ultimo_numero += 1  # Incrementa l'ultimo numero per il tipo di documento
                self.numero = tipo_doc.ultimo_numero
                tipo_doc.save()  # Salva il nuovo ultimo numero
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.cliente.concessionario} | {self.cliente.nome} |  {self.tipo_documento.nome} | {self.data} | {self.numero}"

class Fornitore(models.Model):
    nome = models.CharField(max_length=255)
    indirizzo = models.TextField()
    partita_iva = models.CharField(max_length=20, blank=True, null=True)
    telefono = models.CharField(max_length=15, blank=True, null=True)

    def __str__(self):
        return self.nome

class Zona(models.Model):
    nome = models.CharField(max_length=100)
    concessionario = models.ForeignKey('Concessionario', on_delete=models.CASCADE, related_name='zone')
    user = models.OneToOneField('auth.user', on_delete=models.CASCADE, related_name='zona')

    def __str__(self):
        return f"{self.nome} | {self.concessionario.nome} | {self.user.get_full_name()}"

class Proprietario(models.Model):
    codice = models.CharField(max_length=30, default="0000")
    nome = models.CharField(max_length=255)
    indirizzo = models.TextField(null=True, blank=True)  # Indirizzo completo
    piva = models.CharField(max_length=20, null=True, blank=True)  # Telefono opzionale

    def __str__(self):
        return f"{self.nome} - {self.piva}"

class ArticoliConcessi(models.Model):
    proprietario = models.ForeignKey('Proprietario', on_delete=models.CASCADE, related_name='articoli_concessi')
    articolo = models.ForeignKey('Articolo', on_delete=models.CASCADE, related_name='concessioni')

    class Meta:
        unique_together = ('proprietario', 'articolo')
        verbose_name = "Articolo Concesso"
        verbose_name_plural = "Articoli Concessi"

    def __str__(self):
        return f"{self.articolo.nome} concesso a {self.proprietario.nome}"

class Carico(models.Model):
    data = models.DateField(default=datetime.now)
    zona = models.ForeignKey('Zona', on_delete=models.CASCADE)
    fornitore = models.ForeignKey('Fornitore', on_delete=models.CASCADE)
    numero = models.CharField(max_length=20, default="") #Numero a cazzo: S1V000003252, insomma.... Più o meno 15 caratteri
    note = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"Carico {self.numero} - {self.fornitore.nome} del {self.data}"

class RigaCarico(models.Model):
    carico = models.ForeignKey('Carico', on_delete=models.CASCADE, related_name="righe")
    articolo = models.ForeignKey('Articolo', on_delete=models.CASCADE)
    quantita = models.PositiveIntegerField()
    lotto = models.CharField(max_length=20)

    def __str__(self):
        return f"Carico Num: {self.carico.numero} | {self.articolo.nome} - Lotto: {self.lotto} - Quantità: {self.quantita}"

class Reso(models.Model):
    data = models.DateField(default=datetime.now)
    zona = models.ForeignKey('Zona', on_delete=models.CASCADE)
    note = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"Reso del {self.data} di {self.zona}"

class RigaReso(models.Model):
    reso = models.ForeignKey('Reso', on_delete=models.CASCADE, related_name="righe")
    articolo = models.ForeignKey('Articolo', on_delete=models.CASCADE)
    quantita = models.PositiveIntegerField()

    def __str__(self):
        return f"Reso del {self.reso.data} | {self.articolo.nome} - Quantità: {self.quantita}"

class TipoFattura(models.Model):
    TIPO_SCELTE = [
        ("TD01", 'Fattura'),
        ("TD04", 'Nota di Credito'),
        ("TD05", 'Nota di Debito'),
    ]
    tipo = models.CharField(max_length=5, choices=TIPO_SCELTE) # Nome breve
    descrizione = models.CharField(max_length=255, blank=True, null=True)
    anno = models.PositiveIntegerField(default=datetime.today().year)
    ultimo_numero = models.PositiveIntegerField(default=0)
    concessionario = models.ForeignKey('Concessionario', on_delete=models.CASCADE)

    def __str__(self):
        return self.descrizione

class Fattura(models.Model):
    CONDIZIONI = [
        ("TP01", 'Pagamento a rate'),
        ("TP02", 'Pagamento completo'),
        ("TP03", 'Anticipo'),
    ]
    MODALITA = [
        ("MP01", 'Contanti'),
        ("MP02", 'Assegno'),
        ("MP03", 'Assegno Circolare'),
        ("MP05", 'Bonifico'),
        ("MP08", 'Carta di Pagamento'),
    ]

    def default_totali():
        return {
            "4": {"imp": 0.0, "iva": 0.0, "tot": 0.0},
            "10": {"imp": 0.0, "iva": 0.0, "tot": 0.0},
            "22": {"imp": 0.0, "iva": 0.0, "tot": 0.0},
            "tot": 0.0,  # Valore totale fattura
        }
    data = models.DateField(default=datetime.now)
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE)
    concessionario = models.ForeignKey('Concessionario', on_delete=models.CASCADE)
    tipo_fattura = models.ForeignKey('TipoFattura', on_delete=models.CASCADE)
    numero = models.CharField(max_length=20, default="")
    condizioni_pagamento = models.CharField(max_length=4, choices=CONDIZIONI)
    scadenza_pagamento = models.DateField(default=(datetime.now))
    modalita_pagamento = models.CharField(max_length=4, choices=MODALITA)
    totali = models.JSONField(default=default_totali)
    # Struttura totali:
    # {"4": {"imp": 0.0, "iva": 0.0, "tot": 0.0}, ... }
    xml_file = models.TextField(default="", null=True, blank=True)
    pdf_file = models.TextField(default="", null=True, blank=True)
    note = models.TextField(null=True, blank=True)

    def aggiorna_totali(self):
        # Reset dei totali
        nuovi_totali = {
            "4": {"imp": 0.0, "iva": 0.0, "tot": 0.0},
            "10": {"imp": 0.0, "iva": 0.0, "tot": 0.0},
            "22": {"imp": 0.0, "iva": 0.0, "tot": 0.0},
            "tot": 0.0,
        }
        rigafattura = RigaFattura.objects.filter(fattura=self)
        # Itera su tutte le righe della fattura
        for riga in rigafattura.all():
            aliquota = str(riga.iva)  # '4', '10', '22'
            nuovi_totali[aliquota]["imp"] += float(riga.imp)
            nuovi_totali[aliquota]["iva"] += float(riga.tot_iva)
            nuovi_totali[aliquota]["tot"] += float(riga.imp) + float(riga.tot_iva)

        nuovi_totali["tot"] = nuovi_totali["4"]["tot"] + nuovi_totali["10"]["tot"] + nuovi_totali["22"]["tot"]
        # Aggiorna il campo totali
        self.totali = nuovi_totali
        self.save()  # Salva la fattura con i nuovi totali

    def save(self, *args, **kwargs):
        skip_auto_number = False
        if not self.id and not skip_auto_number:  # Controlla se la fattura è nuova
            with transaction.atomic():  # Blocca la transazione per garantire unicità
                anno_fatt = self.data.year
                tipi_fattura = TipoFattura.objects.filter(anno=anno_fatt)
                nuovo_numero = self.tipo_fattura.ultimo_numero + 1  # Incrementa l'ultimo numero fattura
                for tipo_fatt in tipi_fattura:
                    tipo_fatt.ultimo_numero = nuovo_numero
                    tipo_fatt.save()  # Salva il nuovo ultimo numero
                self.numero = nuovo_numero
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tipo_fattura.descrizione} N. {self.numero} di {self.concessionario.nome} a {self.cliente.nome} del {self.data}"

class RigaFattura(models.Model):
    IVA_SCELTE = [
        (4, '4%'),
        (10, '10%'),
        (22, '22%'),
    ]
    numero_linea = models.CharField(max_length=4, null=True, blank=True)
    fattura = models.ForeignKey('Fattura', on_delete=models.CASCADE, related_name="righe")
    articolo = models.ForeignKey('Articolo', on_delete=models.CASCADE)
    prezzo = models.DecimalField(max_digits=10, decimal_places=3, default=0.0)
    quantita = models.PositiveIntegerField()
    imp = models.DecimalField(max_digits=20, decimal_places=2, default=0.0)
    iva = models.PositiveSmallIntegerField(choices=IVA_SCELTE, default=4)
    tot_iva = models.DecimalField(max_digits=20, decimal_places=2, default=0.0)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            from .utils.genera_fattura import genera_fattura_xml
            if not self.prezzo:
                self.prezzo = self.articolo.prezzo
            self.imp = (Decimal(self.prezzo) * Decimal(self.quantita))
            bias_imp = self.imp + Decimal('0.0000001')
            # DEBUG print(bias_imp)
            self.imp = bias_imp.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            self.tot_iva = Decimal(Decimal(self.imp) * (Decimal(self.iva) / 100))
            bias_iva = self.tot_iva + Decimal('0.0001')
            # DEBUG print(bias_iva)
            self.tot_iva = bias_iva.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Fattura Num: {self.fattura.tipo_fattura} {self.fattura.numero} | {self.articolo.nome} | Quantità: {self.quantita}"

class SchedaTV(models.Model):
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='schede_tv')
    tipo_documento = models.ForeignKey('TipoDocumento', on_delete=models.CASCADE)
    data = models.DateField(default=timezone.now)  # Usa DateField invece di mese/anno separati
    numero = models.PositiveIntegerField()

    def save(self, *args, **kwargs):
        if not self.id:  # Controlla se la bolla è nuova
            with transaction.atomic():  # Blocca la transazione per garantire unicità
                tipo_doc = self.tipo_documento
                tipo_doc.ultimo_numero += 1  # Incrementa l'ultimo numero per il tipo di documento
                self.numero = tipo_doc.ultimo_numero
                tipo_doc.save()  # Salva il nuovo ultimo numero
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.data.strftime('%m/%Y')} | {self.cliente} N.{self.numero}"

class RigaSchedaTV(models.Model):
    scheda = models.ForeignKey('SchedaTV', on_delete=models.CASCADE, related_name='righe')
    giorno = models.PositiveSmallIntegerField(choices=[(i, str(i)) for i in range(1, 32)]) # Una scelta di numeri da 1 a 31 per il giorno del mese.
    articolo = models.ForeignKey('Articolo', on_delete=models.CASCADE)
    quantita = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.scheda} - Giorno {self.giorno}: {self.quantita}x {self.articolo.nome}"

class PrezziPersonalizzati(models.Model):
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE)
    articolo = models.ForeignKey('Articolo', on_delete=models.CASCADE)
    prezzo = models.DecimalField(max_digits=10, decimal_places=3, default=0.0)

    @property
    def prezzo_ivato(self):
        """Calcola il prezzo con IVA inclusa."""
        return Decimal(format(self.prezzo * Decimal(1 + self.articolo.iva / 100), ".3f"))

    def __str__(self):
        return f"{self.cliente.nome} - {self.articolo}: {self.prezzo}, iva: {self.prezzo_ivato}"
