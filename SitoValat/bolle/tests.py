from django.test import TestCase
from datetime import datetime
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from django.contrib.auth import get_user_model

from .models import (
    Concessionario,
    TipoDocumento,
    TipoDocCounter,
    Cliente,
    Proprietario,
    Bolla,
    SchedaTV,
)

class AnnualCountersTests(TestCase):
    def setUp(self):
        self.conc = Concessionario.objects.create(
            nome="Conc",
            indirizzo="x",
            via="x",
            cap="00000",
            citta="x",
            provincia="SA",
            partita_iva="IT00000000000",
        )

        self.tipo_doc = TipoDocumento.objects.create(
            nome="CLS",
            descrizione="Test",
            concessionario=self.conc,
            ultimo_numero=0,  # cache field (optional)
        )

        self.tipo_doc_NTV = TipoDocumento.objects.create(
            nome="NTV",
            descrizione="Test",
            concessionario = self.conc,
            ultimo_numero=0,
        )

        self.prop = Proprietario.objects.create(
            nome="Prop",
            piva="IT00000000000",
        )

        self.cliente = Cliente.objects.create(
            nome="Cliente",
            concessionario=self.conc,
            via="x",
            cap="00000",
            citta="x",
            provincia="SA",
            piva="IT00000000000",
            proprietario=self.prop,
            tipo_documento_predefinito=self.tipo_doc,
        )
        self.cliente2 = Cliente.objects.create(
            nome="Cliente2",
            concessionario=self.conc,
            via="x2",
            cap="00020",
            citta="x",
            provincia="SA",
            piva="IT00000000000",
            proprietario=self.prop,
            tipo_documento_predefinito=self.tipo_doc,
        )
        User = get_user_model()
        self.user = User.objects.create_user(username="u", password="p")
        self.client.login(username="u", password="p")

    def test_bolla_increments_same_year(self):
        dt = self.aware(2025, 12, 30)
        b1 = Bolla.objects.create(cliente=self.cliente, tipo_documento=self.tipo_doc, data=dt, note="")
        b2 = Bolla.objects.create(cliente=self.cliente, tipo_documento=self.tipo_doc, data=dt, note="")
        self.assertEqual(b1.numero, 1)
        self.assertEqual(b2.numero, 2)

        counter = TipoDocCounter.objects.get(tipo=self.tipo_doc, anno=2025)
        self.assertEqual(counter.ultimo_numero, 2)

    def test_bolla_resets_new_year(self):
        dt_2025 = self.aware(2025, 12, 31)
        dt_2026 = self.aware(2026, 1, 1)

        b1 = Bolla.objects.create(cliente=self.cliente, tipo_documento=self.tipo_doc, data=dt_2025, note="")
        b2 = Bolla.objects.create(cliente=self.cliente, tipo_documento=self.tipo_doc, data=dt_2025, note="")
        b3 = Bolla.objects.create(cliente=self.cliente, tipo_documento=self.tipo_doc, data=dt_2026, note="")

        self.assertEqual(b1.numero, 1)
        self.assertEqual(b2.numero, 2)
        self.assertEqual(b3.numero, 1)

        c2025 = TipoDocCounter.objects.get(tipo=self.tipo_doc, anno=2025)
        c2026 = TipoDocCounter.objects.get(tipo=self.tipo_doc, anno=2026)
        self.assertEqual(c2025.ultimo_numero, 2)
        self.assertEqual(c2026.ultimo_numero, 1)

        print(f"{str(b1.data.year)} e num: {str(b1.numero)}")
        print(f"{str(b2.data.year)} e num: {str(b2.numero)}")
        print(f"{str(b3.data.year)} e num: {str(b3.numero)}")

        self.client.login(username="u", password="p")
        resp = self.client.post(reverse("bolla-delete", kwargs={"pk": b2.pk}))
        self.assertEqual(resp.status_code, 302)

        c2025.refresh_from_db()
        self.assertEqual(c2025.ultimo_numero, 1)

    def aware(self, y, m, d, hh=10, mm=0):
        return timezone.make_aware(datetime(y, m, d, hh, mm, 0))

    def test_schedatv_increments_same_year(self):
        dt = self.aware(2025, 12, 30)

        s1 = SchedaTV.objects.create(cliente=self.cliente, tipo_documento=self.tipo_doc, data=dt)
        s2 = SchedaTV.objects.create(cliente=self.cliente, tipo_documento=self.tipo_doc, data=dt)

        self.assertEqual(s1.numero, 1)
        self.assertEqual(s2.numero, 2)

        c2025 = TipoDocCounter.objects.get(tipo=self.tipo_doc, anno=2025)
        self.assertEqual(c2025.ultimo_numero, 2)

    def test_schedatv_resets_new_year(self):
        dt_2025 = self.aware(2025, 12, 31)
        dt_2026 = self.aware(2026, 1, 1)

        s1 = SchedaTV.objects.create(cliente=self.cliente, tipo_documento=self.tipo_doc, data=dt_2025)
        s2 = SchedaTV.objects.create(cliente=self.cliente, tipo_documento=self.tipo_doc, data=dt_2025)
        s3 = SchedaTV.objects.create(cliente=self.cliente, tipo_documento=self.tipo_doc, data=dt_2026)

        self.assertEqual(s1.numero, 1)
        self.assertEqual(s2.numero, 2)
        self.assertEqual(s3.numero, 1)

        c2025 = TipoDocCounter.objects.get(tipo=self.tipo_doc_NTV, anno=2025)
        c2026 = TipoDocCounter.objects.get(tipo=self.tipo_doc_NTV, anno=2026)
        self.assertEqual(c2025.ultimo_numero, 2)
        self.assertEqual(c2026.ultimo_numero, 1)

    def test_delete_last_decrements_counter(self):
        dt = self.aware(2026, 1, 2)
        s1 = SchedaTV.objects.create(cliente=self.cliente, tipo_documento=self.tipo_doc_NTV, data=dt)
        s2 = SchedaTV.objects.create(cliente=self.cliente, tipo_documento=self.tipo_doc_NTV, data=dt)  # last

        resp = self.client.post(reverse("schedatv-delete", kwargs={"pk": s2.pk}))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(SchedaTV.objects.filter(pk=s2.pk).exists())

        c2026 = TipoDocCounter.objects.get(tipo=self.tipo_doc, anno=2026)
        self.assertEqual(c2026.ultimo_numero, 1)

