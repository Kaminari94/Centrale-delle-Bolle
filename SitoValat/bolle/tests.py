from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.core.exceptions import PermissionDenied
from datetime import datetime, timedelta
from decimal import Decimal
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
    Articolo,
    Categoria,
    Zona,
    Reso,
    RigaReso,
    Carico,
    RigaCarico,
    RigaBolla,
    Fornitore,
)
from .utils.riepilogo import calcola_riep_giornaliero

class ArticoloListViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="articoli", password="test-password")
        self.categoria_b = Categoria.objects.create(nome="Bevande", ordine=2)
        self.categoria_a = Categoria.objects.create(nome="Alimentari", ordine=1)
        self.categoria_vuota = Categoria.objects.create(nome="Vuota", ordine=0)
        self.articolo_b = Articolo.objects.create(
            nome="B200",
            descrizione="Descrizione completa",
            categoria=self.categoria_b,
            iva=22,
            prezzo=Decimal("10.125"),
            costo=Decimal("5.555"),
        )
        self.articolo_a = Articolo.objects.create(
            nome="A100",
            descrizione=None,
            categoria=self.categoria_a,
            iva=4,
            prezzo=Decimal("1.005"),
            costo=Decimal("0.000"),
        )

    def test_login_required(self):
        response = self.client.get(reverse("articoli-list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_lists_all_articles_in_category_order(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("articoli-list"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "articoli/articolo_list.html")
        self.assertQuerySetEqual(
            response.context["articoli"],
            [self.articolo_a, self.articolo_b],
        )
        self.assertQuerySetEqual(
            response.context["categorie"],
            [self.categoria_a, self.categoria_b],
        )

    def test_renders_prices_costs_without_transfer_prices(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("articoli-list"))

        self.assertContains(response, "Prezzo IVA incl.")
        self.assertContains(response, "Costo IVA incl.")
        self.assertContains(response, "Descrizione completa")
        self.assertContains(response, "&mdash;")
        self.assertNotContains(response, "transfert")

    def test_vat_properties_use_exact_decimal_arithmetic(self):
        self.assertEqual(self.articolo_b.prezzo_ivato, Decimal("12.35250"))
        self.assertEqual(self.articolo_b.costo_ivato, Decimal("6.77710"))

    def test_empty_list_has_readable_fallback(self):
        Articolo.objects.all().delete()
        self.client.force_login(self.user)

        response = self.client.get(reverse("articoli-list"))

        self.assertContains(response, "Nessun articolo disponibile.")
        self.assertNotContains(response, 'id="articoli-table"')

    def test_homepage_contains_article_navigation_and_cards(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("home"))

        self.assertContains(response, reverse("articoli-list"), count=3)
        self.assertContains(response, "bi-tags", count=2)


class RiepilogoGiornalieroTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.concessionario = Concessionario.objects.create(
            nome="Conc riepilogo",
            indirizzo="x",
            via="x",
            cap="00000",
            citta="x",
            provincia="SA",
            partita_iva="IT11111111111",
        )
        self.tipo_documento = TipoDocumento.objects.create(
            nome="CLS",
            concessionario=self.concessionario,
        )
        self.utente_zona = User.objects.create_user(username="zona-richiesta")
        self.zona = Zona.objects.create(
            nome="Zona richiesta",
            concessionario=self.concessionario,
            user=self.utente_zona,
        )
        self.altra_zona = Zona.objects.create(
            nome="Altra zona",
            concessionario=self.concessionario,
            user=User.objects.create_user(username="altra-zona"),
        )
        self.cliente_zona = Cliente.objects.create(
            nome="Cliente zona richiesta",
            concessionario=self.concessionario,
            piva="IT22222222222",
            tipo_documento_predefinito=self.tipo_documento,
            zona=self.zona,
        )
        self.cliente_altra_zona = Cliente.objects.create(
            nome="Cliente altra zona",
            concessionario=self.concessionario,
            piva="IT33333333333",
            tipo_documento_predefinito=self.tipo_documento,
            zona=self.altra_zona,
        )

    def richiesta(self, data_giorno="2026-08-28", zona=None, utente=None):
        request = RequestFactory().get(
            reverse("riepilogo-giornaliero"),
            {"data_giorno": data_giorno, "zona": zona or self.zona.pk},
        )
        request.user = utente or self.utente_zona
        return request

    def test_include_solo_bolle_dei_clienti_della_zona_richiesta(self):
        giorno = timezone.make_aware(datetime(2026, 8, 28, 10))
        bolla_zona = Bolla.objects.create(
            cliente=self.cliente_zona,
            tipo_documento=self.tipo_documento,
            data=giorno,
        )
        Bolla.objects.create(
            cliente=self.cliente_altra_zona,
            tipo_documento=self.tipo_documento,
            data=giorno,
        )
        # Il calcolo cerca sempre un reso precedente disponibile.
        Reso.objects.create(data=giorno.date() - timedelta(days=1), zona=self.zona)

        risultato = calcola_riep_giornaliero(self.richiesta())

        self.assertQuerySetEqual(risultato["bolle_del_giorno"], [bolla_zona])

    def test_utente_di_zona_non_puo_richiedere_un_altra_zona(self):
        with self.assertRaises(PermissionDenied):
            calcola_riep_giornaliero(self.richiesta(zona=self.altra_zona.pk))

    def test_superuser_puo_richiedere_qualsiasi_zona(self):
        superuser = get_user_model().objects.create_superuser(
            username="amministratore",
            password="test-password",
            email="admin@example.com",
        )

        risultato = calcola_riep_giornaliero(
            self.richiesta(zona=self.altra_zona.pk, utente=superuser)
        )

        self.assertEqual(risultato["zona"], self.altra_zona)

    def test_concessionario_puo_richiedere_le_proprie_zone(self):
        utente_concessionario = get_user_model().objects.create_user(
            username="concessionario-riepilogo"
        )
        self.concessionario.user = utente_concessionario
        self.concessionario.save(update_fields=["user"])

        risultato = calcola_riep_giornaliero(
            self.richiesta(zona=self.altra_zona.pk, utente=utente_concessionario)
        )

        self.assertEqual(risultato["zona"], self.altra_zona)

    def test_utente_senza_ruolo_non_puo_richiedere_zone(self):
        utente = get_user_model().objects.create_user(username="senza-ruolo")

        with self.assertRaises(PermissionDenied):
            calcola_riep_giornaliero(self.richiesta(utente=utente))

    def test_parametri_mancanti_reindirizzano_al_menu(self):
        self.client.force_login(self.utente_zona)

        response = self.client.get(reverse("riepilogo-giornaliero"))

        self.assertRedirects(response, reverse("menu-riepiloghi"))

    def test_data_e_zona_non_valide_reindirizzano_al_menu(self):
        self.client.force_login(self.utente_zona)

        data_non_valida = self.client.get(
            reverse("riepilogo-giornaliero"),
            {"data_giorno": "28/08/2026", "zona": self.zona.pk},
        )
        zona_non_valida = self.client.get(
            reverse("riepilogo-giornaliero"),
            {"data_giorno": "2026-08-28", "zona": "inesistente"},
        )

        self.assertRedirects(data_non_valida, reverse("menu-riepiloghi"))
        self.assertRedirects(zona_non_valida, reverse("menu-riepiloghi"))

    def test_view_html_e_xlsx_restano_funzionanti(self):
        self.client.force_login(self.utente_zona)
        parametri = {"data_giorno": "2026-08-28", "zona": self.zona.pk}

        html = self.client.get(reverse("riepilogo-giornaliero"), parametri)
        xlsx = self.client.get(reverse("riepilogo-stampa"), parametri)

        self.assertEqual(html.status_code, 200)
        self.assertTemplateUsed(html, "riepiloghi/riepilogo_giornaliero.html")
        self.assertEqual(xlsx.status_code, 200)
        self.assertEqual(
            xlsx["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertGreater(len(xlsx.content), 0)

    def test_menu_precompila_la_data_del_riepilogo_con_oggi(self):
        self.client.force_login(self.utente_zona)

        response = self.client.get(reverse("menu-riepiloghi"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'value="{timezone.localdate().isoformat()}"',
            count=2,
        )

    def test_calcolo_aggregato_mantiene_output_e_valori(self):
        categoria = Categoria.objects.create(nome="Test riepilogo")
        articolo = Articolo.objects.create(
            nome="ART-1",
            categoria=categoria,
            prezzo=Decimal("1.000"),
            iva=4,
        )
        fornitore = Fornitore.objects.create(
            nome="Fornitore",
            indirizzo="x",
        )
        carico = Carico.objects.create(
            data=datetime(2026, 8, 27).date(),
            zona=self.zona,
            fornitore=fornitore,
        )
        RigaCarico.objects.create(
            carico=carico,
            articolo=articolo,
            quantita=10,
            lotto="L1",
        )
        reso_precedente = Reso.objects.create(
            data=datetime(2026, 8, 26).date(),
            zona=self.zona,
        )
        RigaReso.objects.create(reso=reso_precedente, articolo=articolo, quantita=2)
        reso_attuale = Reso.objects.create(
            data=datetime(2026, 8, 28).date(),
            zona=self.zona,
        )
        RigaReso.objects.create(reso=reso_attuale, articolo=articolo, quantita=1)
        bolla = Bolla.objects.create(
            cliente=self.cliente_zona,
            tipo_documento=self.tipo_documento,
            data=timezone.make_aware(datetime(2026, 8, 28, 10)),
        )
        RigaBolla.objects.create(bolla=bolla, articolo=articolo, quantita=3)

        risultato = calcola_riep_giornaliero(self.richiesta())
        riga = risultato["riepilogo"][articolo.nome]

        self.assertEqual(
            set(riga),
            {
                "nome", "carico_prec", "reso_prec", "carico_tot", "reso_att",
                "bolla_nt", "bolla_cls", "bolla_totale", "quantita_venduta",
                "tot_euro",
            },
        )
        self.assertEqual(riga["carico_prec"], 10)
        self.assertEqual(riga["reso_prec"], 2)
        self.assertEqual(riga["carico_tot"], 12)
        self.assertEqual(riga["reso_att"], 1)
        self.assertEqual(riga["bolla_cls"], 3)
        self.assertEqual(riga["bolla_totale"], 3)
        self.assertEqual(riga["quantita_venduta"], 8)
        self.assertEqual(riga["tot_euro"], "8.32")
        self.assertEqual(risultato["totale"], Decimal("8.32"))
        self.assertEqual(
            risultato["documenti"],
            [{
                "cliente": self.cliente_zona.nome,
                "tipo": "CLS",
                "righe": [{"articolo": articolo.nome, "quantita": "3"}],
            }],
        )

    def test_numero_query_non_cresce_con_il_numero_di_articoli(self):
        categoria = Categoria.objects.create(nome="Prestazioni")
        Articolo.objects.bulk_create(
            [Articolo(nome=f"PERF-{indice}", categoria=categoria) for indice in range(50)]
        )

        with CaptureQueriesContext(connection) as query:
            calcola_riep_giornaliero(self.richiesta())

        self.assertLessEqual(len(query), 9)

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

