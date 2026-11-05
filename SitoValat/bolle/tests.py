from io import BytesIO
from unittest.mock import patch

from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection, IntegrityError, transaction
from django.core.exceptions import PermissionDenied, ValidationError
from datetime import datetime, timedelta
from decimal import Decimal
from django.utils import timezone
from django.urls import reverse
from django.contrib.auth import get_user_model
from PyPDF2 import PdfReader

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
    TipoFattura,
    Fattura,
    RigaFattura,
    current_year,
)
from .utils.riepilogo import calcola_riep_giornaliero
from .utils.genera_fattura import genera_fattura_xml

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

        c2025 = TipoDocCounter.objects.get(tipo=self.tipo_doc, anno=2025)
        c2026 = TipoDocCounter.objects.get(tipo=self.tipo_doc, anno=2026)
        self.assertEqual(c2025.ultimo_numero, 2)
        self.assertEqual(c2026.ultimo_numero, 1)

    def test_delete_last_decrements_counter(self):
        dt = self.aware(2026, 1, 2)
        s1 = SchedaTV.objects.create(cliente=self.cliente, tipo_documento=self.tipo_doc_NTV, data=dt)
        s2 = SchedaTV.objects.create(cliente=self.cliente, tipo_documento=self.tipo_doc_NTV, data=dt)  # last

        resp = self.client.post(reverse("schedatv-delete", kwargs={"pk": s2.pk}))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(SchedaTV.objects.filter(pk=s2.pk).exists())

        c2026 = TipoDocCounter.objects.get(tipo=self.tipo_doc_NTV, anno=2026)
        self.assertEqual(c2026.ultimo_numero, 1)


class TipoFatturaDefaultAndNumberingTests(TestCase):
    def setUp(self):
        self.conc = Concessionario.objects.create(
            nome='Concessionario fatture', indirizzo='Via Roma', via='Via Roma', cap='84025',
            citta='Eboli', provincia='SA', partita_iva='IT00000000002',
        )
        self.cliente = Cliente.objects.create(
            nome='Cliente fatture', concessionario=self.conc, via='Via Test', cap='84025',
            citta='Eboli', provincia='SA', piva='IT00000000002', pec='test@example.com',
        )

    def test_tipo_fattura_usa_l_anno_corrente_alla_creazione(self):
        tipo = TipoFattura.objects.create(tipo='TD01', descrizione='Fattura', concessionario=self.conc)

        self.assertEqual(tipo.anno, current_year())

    def test_numerazione_fatture_resta_progressiva(self):
        tipo = TipoFattura.objects.create(tipo='TD01', descrizione='Fattura', concessionario=self.conc)
        oggi = timezone.localdate()
        comune = {
            'cliente': self.cliente,
            'concessionario': self.conc,
            'tipo_fattura': tipo,
            'data': oggi,
            'condizioni_pagamento': 'TP02',
            'scadenza_pagamento': oggi,
            'modalita_pagamento': 'MP01',
        }

        prima = Fattura.objects.create(**comune)
        seconda = Fattura.objects.create(**comune)

        self.assertEqual(prima.numero, '1')
        self.assertEqual(seconda.numero, '2')


class OrdineNTVTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='operatore-eboli', password='test-password')
        self.user_seconda_zona = User.objects.create_user(username='operatore-seconda-zona', password='test-password')
        self.conc = Concessionario.objects.create(
            nome='Concessionario', indirizzo='Via Roma', via='Via Roma', cap='84025',
            citta='Eboli', provincia='SA', partita_iva='IT00000000001',
        )
        self.zona = Zona.objects.create(nome='Eboli', concessionario=self.conc, user=self.user)
        self.zona_seconda = Zona.objects.create(
            nome='Seconda zona', concessionario=self.conc, user=self.user_seconda_zona,
        )
        self.tipo_ntv = TipoDocumento.objects.create(nome='NTV', concessionario=self.conc)
        self.tipo_nt = TipoDocumento.objects.create(nome='NT', concessionario=self.conc)

    def cliente(self, nome, zona=None, ordine_ntv=None, tipo=None, ignorare=False):
        return Cliente.objects.create(
            nome=nome,
            codice=nome[:10],
            concessionario=self.conc,
            via='Via Test', cap='84025', citta='Eboli', provincia='SA', piva=f'IT{nome}',
            pec='test@example.com',
            zona=zona or self.zona,
            tipo_documento_predefinito=tipo or self.tipo_ntv,
            ordine_ntv=ordine_ntv,
            ignorare=ignorare,
        )

    def scheda(self, cliente):
        return SchedaTV.objects.create(
            cliente=cliente,
            tipo_documento=self.tipo_ntv,
            data=datetime(2026, 9, 1).date(),
        )

    def test_ordine_ntv_null_e_positivo_sono_validi(self):
        senza_ordine = self.cliente('Senza ordine')
        con_ordine = self.cliente('Con ordine', ordine_ntv=10)

        senza_ordine.full_clean()
        con_ordine.full_clean()
        self.assertIsNone(senza_ordine.ordine_ntv)
        self.assertEqual(con_ordine.ordine_ntv, 10)

    def test_ordine_ntv_zero_non_e_valido(self):
        cliente = self.cliente('Ordine zero')
        cliente.ordine_ntv = 0

        with self.assertRaises(ValidationError):
            cliente.full_clean()

    def test_ordine_ntv_non_ntv_non_e_valido(self):
        cliente = self.cliente('Cliente NT', ordine_ntv=10, tipo=self.tipo_nt)

        with self.assertRaises(ValidationError):
            cliente.full_clean()

    def test_ordine_ntv_duplicato_nella_stessa_zona_non_e_consentito(self):
        self.cliente('Primo', ordine_ntv=10)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.cliente('Secondo', ordine_ntv=10)

    def test_stesso_ordine_in_zone_diverse_e_consentito(self):
        primo = self.cliente('Eboli', ordine_ntv=10)
        secondo = self.cliente('Altra zona', zona=self.zona_seconda, ordine_ntv=10)

        self.assertEqual(primo.ordine_ntv, secondo.ordine_ntv)

    def test_pagina_ordine_salva_e_permette_scambiare_posizioni(self):
        primo = self.cliente('Primo', ordine_ntv=10)
        secondo = self.cliente('Secondo', ordine_ntv=20)
        self.client.force_login(self.user)

        response = self.client.post(reverse('ordine-ntv'), {
            f'ordine_{primo.pk}': '20',
            f'ordine_{secondo.pk}': '10',
        }, follow=True)

        primo.refresh_from_db()
        secondo.refresh_from_db()
        self.assertEqual(primo.ordine_ntv, 20)
        self.assertEqual(secondo.ordine_ntv, 10)
        self.assertContains(response, 'Ordine NTV salvato.')

    def test_pagina_ordine_rifiuta_duplicati_senza_modificare_dati(self):
        primo = self.cliente('Primo', ordine_ntv=10)
        secondo = self.cliente('Secondo', ordine_ntv=20)
        self.client.force_login(self.user)

        response = self.client.post(reverse('ordine-ntv'), {
            f'ordine_{primo.pk}': '10',
            f'ordine_{secondo.pk}': '10',
        })

        primo.refresh_from_db()
        secondo.refresh_from_db()
        self.assertEqual(primo.ordine_ntv, 10)
        self.assertEqual(secondo.ordine_ntv, 20)
        self.assertContains(response, 'duplicato nella zona Eboli')

    def test_lista_schedetv_ordina_per_ordine_ntv_con_null_in_fondo(self):
        ultimo = self.scheda(self.cliente('Senza ordine'))
        secondo = self.scheda(self.cliente('Secondo', ordine_ntv=20))
        primo = self.scheda(self.cliente('Primo', ordine_ntv=10))
        self.client.force_login(self.user)

        response = self.client.get(reverse('schedetv-list'), {
            'data_inizio': '2026-09-01', 'data_fine': '2026-09-30',
        })

        self.assertEqual(list(response.context['schede_tv']), [primo, secondo, ultimo])
        self.assertContains(response, 'Ordine da configurare')

    def test_precedente_prossimo_e_contatore_usano_solo_clienti_ordinati(self):
        precedente = self.scheda(self.cliente('Primo', ordine_ntv=10))
        corrente = self.scheda(self.cliente('Secondo', ordine_ntv=20))
        self.scheda(self.cliente('Senza ordine'))
        successiva = self.scheda(self.cliente('Terzo', ordine_ntv=30))
        self.client.force_login(self.user)

        response = self.client.get(reverse('schedatv-update', kwargs={'pk': corrente.pk}))

        self.assertEqual(response.context['sequence_index'], 2)
        self.assertEqual(response.context['sequence_total'], 3)
        self.assertEqual(response.context['previous_scheda'], precedente)
        self.assertEqual(response.context['next_scheda'], successiva)
        self.assertContains(response, 'Cliente 2 di 3')

        response_primo = self.client.get(reverse('schedatv-update', kwargs={'pk': precedente.pk}))
        self.assertEqual(response_primo.status_code, 200)
        self.assertContains(response_primo, 'Cliente 1 di 3')

    def test_scheda_senza_ordine_non_entra_nella_sequenza_ma_resta_accessibile(self):
        senza_ordine = self.scheda(self.cliente('Senza ordine'))
        self.scheda(self.cliente('Ordinato', ordine_ntv=10))
        self.client.force_login(self.user)

        response = self.client.get(reverse('schedatv-update', kwargs={'pk': senza_ordine.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['sequence_index'])
        self.assertEqual(response.context['sequence_total'], 0)
        self.assertContains(response, 'Ordine NTV da configurare')


class FattureCumulativePdfOrderingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.conc = Concessionario.objects.create(
            nome='Concessionario PDF', indirizzo='Via Roma', via='Via Roma', cap='84025',
            citta='Eboli', provincia='SA', partita_iva='IT00000000003',
        )
        self.zona_1 = Zona.objects.create(
            nome='Prima zona', concessionario=self.conc,
            user=User.objects.create_user(username='pdf-zona-1', password='test-password'),
        )
        self.zona_2 = Zona.objects.create(
            nome='Seconda zona', concessionario=self.conc,
            user=User.objects.create_user(username='pdf-zona-2', password='test-password'),
        )
        self.tipo_ntv = TipoDocumento.objects.create(nome='NTV', concessionario=self.conc)
        self.tipo_nt = TipoDocumento.objects.create(nome='NT', concessionario=self.conc)
        self.tipo_fattura = TipoFattura.objects.create(
            tipo='TD01', descrizione='Fattura', anno=2026, concessionario=self.conc,
        )
        self.tipo_nota_credito = TipoFattura.objects.create(
            tipo='TD04', descrizione='Nota di Credito', anno=2026, concessionario=self.conc,
        )

    def cliente(self, nome, zona, tipo_documento, ordine_ntv=None):
        return Cliente.objects.create(
            nome=nome, concessionario=self.conc, via='Via Test', cap='84025',
            citta='Eboli', provincia='SA', piva=f'IT-{nome}', pec='test@example.com',
            zona=zona, tipo_documento_predefinito=tipo_documento, ordine_ntv=ordine_ntv,
        )

    def fattura(self, cliente, tipo_fattura, giorno, numero):
        fattura = Fattura.objects.create(
            cliente=cliente, concessionario=self.conc, tipo_fattura=tipo_fattura,
            data=datetime(2026, 9, giorno).date(), condizioni_pagamento='TP02',
            scadenza_pagamento=datetime(2026, 9, giorno).date(), modalita_pagamento='MP01',
        )
        Fattura.objects.filter(pk=fattura.pk).update(numero=numero)
        fattura.refresh_from_db()
        return fattura

    def test_pdf_cumulativo_ordina_gruppi_e_mantiene_pagine_consecutive(self):
        ntv_zona_1 = self.cliente('NTV zona 1', self.zona_1, self.tipo_ntv, ordine_ntv=10)
        ntv_zona_2 = self.cliente('NTV zona 2', self.zona_2, self.tipo_ntv, ordine_ntv=10)
        ntv_senza_ordine = self.cliente('NTV senza ordine', self.zona_1, self.tipo_ntv)
        cliente_nt = self.cliente('Cliente NT', self.zona_1, self.tipo_nt)

        prima_stesso_cliente = self.fattura(ntv_zona_1, self.tipo_fattura, 2, '10')
        seconda_stesso_cliente = self.fattura(ntv_zona_1, self.tipo_fattura, 2, '2')
        ordinata_altra_zona = self.fattura(ntv_zona_2, self.tipo_fattura, 1, '1')
        senza_ordine = self.fattura(ntv_senza_ordine, self.tipo_fattura, 1, '1')
        fattura_nt = self.fattura(cliente_nt, self.tipo_fattura, 1, '1')
        nota_credito_ntv = self.fattura(ntv_zona_1, self.tipo_nota_credito, 1, '1')

        selezionate = set(Fattura.objects.filter(data__month=9, data__year=2026).values_list('pk', flat=True))
        viste = []

        def disegna_fattura_di_prova(canvas, fattura):
            viste.append(fattura.pk)
            canvas.drawString(20, 800, f'fattura:{fattura.pk}:pagina:1')
            if fattura.pk == prima_stesso_cliente.pk:
                canvas.showPage()
                canvas.drawString(20, 800, f'fattura:{fattura.pk}:pagina:2')

        with patch('bolle.views.disegna_fattura', side_effect=disegna_fattura_di_prova):
            response = self.client.get(reverse('scarica-tutte-pdf'), {
                'data_inizio': '01 Settembre 2026',
                'data_fine': '30 Settembre 2026',
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(viste), selezionate)
        self.assertEqual(len(viste), len(selezionate))
        self.assertEqual(
            viste,
            [prima_stesso_cliente.pk, seconda_stesso_cliente.pk, ordinata_altra_zona.pk,
             senza_ordine.pk, fattura_nt.pk, nota_credito_ntv.pk],
        )

        pagine = [pagina.extract_text().strip() for pagina in PdfReader(BytesIO(response.content)).pages]
        self.assertEqual(
            pagine,
            [
                f'fattura:{prima_stesso_cliente.pk}:pagina:1',
                f'fattura:{prima_stesso_cliente.pk}:pagina:2',
                f'fattura:{seconda_stesso_cliente.pk}:pagina:1',
                f'fattura:{ordinata_altra_zona.pk}:pagina:1',
                f'fattura:{senza_ordine.pk}:pagina:1',
                f'fattura:{fattura_nt.pk}:pagina:1',
                f'fattura:{nota_credito_ntv.pk}:pagina:1',
            ],
        )


class RigaFatturaDescrizioneSnapshotTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='descrizioni-fatture', password='test-password')
        self.conc = Concessionario.objects.create(
            nome='Concessionario descrizioni', indirizzo='Via Roma', via='Via Roma', cap='84025',
            citta='Eboli', provincia='SA', partita_iva='IT00000000004',
        )
        self.zona = Zona.objects.create(nome='Zona descrizioni', concessionario=self.conc, user=self.user)
        self.categoria = Categoria.objects.create(nome='Prodotti descrizioni')
        self.diciture = Categoria.objects.create(nome='Diciture')
        self.articolo = Articolo.objects.create(
            nome='ART-DESC', descrizione='Testo & <originale>\nseconda riga',
            categoria=self.categoria, iva=22, prezzo=Decimal('4.500'), prezzo_tr=Decimal('5.250'),
            costo=Decimal('1.000'),
        )
        self.altro_articolo = Articolo.objects.create(
            nome='ART-ALTRO', descrizione='Descrizione proposta nuova',
            categoria=self.categoria, iva=10, prezzo=Decimal('3.000'), costo=Decimal('1.000'),
        )
        self.dicitura = Articolo.objects.create(
            nome='DICITURA', descrizione='Testo dicitura', categoria=self.diciture,
            iva=22, prezzo=Decimal('0.000'), costo=Decimal('0.000'),
        )
        self.tipo_documento = TipoDocumento.objects.create(nome='NT', concessionario=self.conc)
        self.tipo_rf = TipoDocumento.objects.create(nome='RF', concessionario=self.conc)
        self.tipo_cls = TipoDocumento.objects.create(nome='CLS', concessionario=self.conc)
        self.cliente = Cliente.objects.create(
            nome='Cliente descrizioni', concessionario=self.conc, via='Via Test', cap='84025',
            citta='Eboli', provincia='SA', piva='IT00000000004', pec='test@example.com',
            zona=self.zona, tipo_documento_predefinito=self.tipo_documento,
        )
        self.tipo_fattura = TipoFattura.objects.create(
            tipo='TD01', descrizione='Fattura', anno=2026, concessionario=self.conc,
        )
        self.fattura = Fattura.objects.create(
            cliente=self.cliente, concessionario=self.conc, tipo_fattura=self.tipo_fattura,
            data=datetime(2026, 9, 30).date(), condizioni_pagamento='TP02',
            scadenza_pagamento=datetime(2026, 9, 30).date(), modalita_pagamento='MP01',
        )
        self.centrale = Cliente.objects.create(
            nome="Centrale del Latte d'Italia", concessionario=self.conc, via='Via Centrale', cap='84025',
            citta='Eboli', provincia='SA', piva='01934250018', pec='centrale@example.com',
            zona=self.zona, tipo_documento_predefinito=self.tipo_rf,
        )
        self.fattura_cls = Fattura.objects.create(
            cliente=self.centrale, concessionario=self.conc, tipo_fattura=self.tipo_fattura,
            data=datetime(2026, 9, 30).date(), condizioni_pagamento='TP02',
            scadenza_pagamento=datetime(2026, 9, 30).date(), modalita_pagamento='MP01',
        )
        self._centrale_setting = override_settings(
            CLIENTE_CENTRALE_LATTE_ID=self.centrale.pk,
        )
        self._centrale_setting.enable()
        self.addCleanup(self._centrale_setting.disable)

    def riga(self, articolo=None, **kwargs):
        return RigaFattura.objects.create(
            fattura=self.fattura, articolo=articolo or self.articolo,
            quantita=kwargs.pop('quantita', 2), prezzo=kwargs.pop('prezzo', Decimal('4.500')),
            iva=kwargs.pop('iva', 22), **kwargs,
        )

    def test_snapshot_nuovo_resta_immutabile_e_xml_usa_il_testo_salvato(self):
        riga = self.riga()
        testo_originale = self.articolo.descrizione
        self.assertEqual(riga.descrizione_snapshot, testo_originale)

        self.articolo.descrizione = 'Descrizione anagrafica modificata'
        self.articolo.save()
        riga.quantita = 7
        riga.prezzo = Decimal('8.000')
        riga.iva = 10
        riga.save()
        riga.refresh_from_db()

        self.assertEqual(riga.descrizione_documento, testo_originale)
        self.assertEqual(riga.descrizione_snapshot, testo_originale)
        with patch('bolle.utils.genera_fattura.genera_pdf_base64', return_value='pdf-base64'):
            xml = genera_fattura_xml(self.fattura)
        self.assertIn('Testo &amp; &lt;originale&gt;', xml)
        self.assertIn('seconda riga', xml)
        self.assertNotIn('Descrizione anagrafica modificata', xml)

    def test_stringa_vuota_e_bulk_create_non_usano_il_fallback(self):
        vuota = self.riga(descrizione_snapshot='')
        RigaFattura.objects.bulk_create([
            RigaFattura(
                fattura=self.fattura, articolo=self.altro_articolo,
                quantita=1, prezzo=Decimal('3.000'), iva=10,
            )
        ])
        da_bulk = RigaFattura.objects.exclude(pk=vuota.pk).latest('pk')
        self.articolo.descrizione = 'Non deve apparire'
        self.articolo.save()

        self.assertEqual(vuota.descrizione_documento, '')
        self.assertEqual(da_bulk.descrizione_snapshot, 'Descrizione proposta nuova')

    def test_riga_storica_fallback_solo_finche_non_viene_acquisita_esplicitamente(self):
        riga = self.riga()
        RigaFattura.objects.filter(pk=riga.pk).update(descrizione_snapshot=None)
        riga.refresh_from_db()
        self.articolo.descrizione = 'Fallback corrente'
        self.articolo.save()

        riga.quantita = 5
        riga.save()
        riga.refresh_from_db()
        self.assertTrue(riga.descrizione_da_fallback)
        self.assertEqual(riga.descrizione_documento, 'Fallback corrente')
        self.assertIsNone(riga.descrizione_snapshot)

        self.client.force_login(self.user)
        pagina = self.client.get(reverse('fattura-update', kwargs={'pk': self.fattura.pk}))
        self.assertContains(pagina, 'lo storico originale non è disponibile')
        self.assertContains(pagina, 'Fallback corrente')
        response = self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura.pk}), {
            'aggiorna_riga': '1', 'riga_id': riga.pk, 'articolo': self.articolo.pk,
            'descrizione': 'Fallback corrente', 'categoria': '0',
        })
        self.assertEqual(response.status_code, 302)
        riga.refresh_from_db()
        self.assertFalse(riga.descrizione_da_fallback)
        self.assertEqual(riga.descrizione_snapshot, 'Fallback corrente')

    def test_recupero_totali_e_rifiutato_per_clienti_diversi_da_centrale(self):
        riga = self.riga(descrizione_snapshot='Testo già salvato')
        self.client.force_login(self.user)

        pagina = self.client.get(reverse('fattura-update', kwargs={'pk': self.fattura.pk}))
        self.assertNotContains(pagina, 'Recupera Totali')
        response = self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura.pk}), {
            'recupera_totali': '1', 'mese': '9', 'anno': '2026', 'categoria': '0',
        }, follow=True)

        self.assertContains(response, 'non è disponibile per questa fattura')
        riga.refresh_from_db()
        self.assertEqual(riga.descrizione_snapshot, 'Testo già salvato')

    def test_recupero_cls_ripetuto_non_duplica_e_preserva_righe_esistenti(self):
        self.articolo.iva = 10
        self.articolo.save()
        Bolla.objects.create(
            cliente=self.cliente,
            tipo_documento=self.tipo_cls,
            data=timezone.make_aware(datetime(2026, 9, 15, 10)),
        )
        bolla = Bolla.objects.latest('pk')
        RigaBolla.objects.create(bolla=bolla, articolo=self.articolo, quantita=2)
        self.client.force_login(self.user)

        pagina = self.client.get(reverse('fattura-update', kwargs={'pk': self.fattura_cls.pk}))
        self.assertContains(pagina, 'Recupera Totali')
        response = self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura_cls.pk}), {
            'recupera_totali': '1', 'mese': '9', 'anno': '2026', 'categoria': '0',
        })
        self.assertEqual(response.status_code, 302)
        recuperata = RigaFattura.objects.get(
            fattura=self.fattura_cls,
            recupero_cls_periodo=datetime(2026, 9, 1).date(),
        )
        self.assertEqual(recuperata.quantita, 2)
        self.assertEqual(recuperata.prezzo, Decimal('5.250'))
        self.assertEqual(recuperata.iva, 22)
        self.assertEqual(recuperata.descrizione_snapshot, self.articolo.descrizione)
        manuale = RigaFattura.objects.create(
            fattura=self.fattura_cls, articolo=self.altro_articolo, quantita=1,
            prezzo=Decimal('3.000'), iva=10, descrizione_snapshot='Manuale personalizzata',
        )
        dicitura = RigaFattura.objects.create(
            fattura=self.fattura_cls, articolo=self.dicitura, quantita=1,
            prezzo=Decimal('0.000'), iva=22, descrizione_snapshot='Dicitura manuale',
        )
        self.assertIsNone(manuale.recupero_cls_periodo)
        self.assertIsNone(dicitura.recupero_cls_periodo)

        recuperata.prezzo = Decimal('8.000')
        recuperata.descrizione_snapshot = 'Recuperata poi personalizzata'
        recuperata.save()
        response = self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura_cls.pk}), {
            'recupera_totali': '1', 'mese': '9', 'anno': '2026', 'categoria': '0',
        }, follow=True)

        self.assertContains(response, 'nessuna riga duplicata')
        self.assertEqual(
            RigaFattura.objects.filter(
                fattura=self.fattura_cls,
                recupero_cls_periodo=datetime(2026, 9, 1).date(),
            ).count(),
            1,
        )
        recuperata.refresh_from_db()
        manuale.refresh_from_db()
        dicitura.refresh_from_db()
        self.assertEqual(recuperata.prezzo, Decimal('8.000'))
        self.assertEqual(recuperata.descrizione_snapshot, 'Recuperata poi personalizzata')
        self.assertEqual(manuale.descrizione_snapshot, 'Manuale personalizzata')
        self.assertEqual(dicitura.descrizione_snapshot, 'Dicitura manuale')

    def test_recupero_cls_conserva_dicitura_gia_presente(self):
        bolla = Bolla.objects.create(
            cliente=self.cliente, tipo_documento=self.tipo_cls,
            data=timezone.make_aware(datetime(2026, 9, 15, 10)),
        )
        RigaBolla.objects.create(bolla=bolla, articolo=self.articolo, quantita=2)
        dicitura = RigaFattura.objects.create(
            fattura=self.fattura_cls, articolo=self.dicitura, quantita=1,
            prezzo=Decimal('0.000'), iva=22, descrizione_snapshot='Dicitura da mantenere',
        )
        self.client.force_login(self.user)

        self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura_cls.pk}), {
            'recupera_totali': '1', 'mese': '9', 'anno': '2026', 'categoria': '0',
        })

        dicitura.refresh_from_db()
        self.assertIsNone(dicitura.recupero_cls_periodo)
        self.assertEqual(dicitura.descrizione_snapshot, 'Dicitura da mantenere')
        recuperata = RigaFattura.objects.get(
            fattura=self.fattura_cls,
            recupero_cls_periodo=datetime(2026, 9, 1).date(),
        )
        self.assertEqual(recuperata.articolo, self.articolo)
        self.assertEqual(recuperata.quantita, 2)

    def test_svuota_righe_elimina_solo_la_fattura_aperta_e_azzera_i_totali(self):
        self.riga()
        altra_fattura = Fattura.objects.create(
            cliente=self.cliente, concessionario=self.conc, tipo_fattura=self.tipo_fattura,
            data=datetime(2026, 9, 30).date(), condizioni_pagamento='TP02',
            scadenza_pagamento=datetime(2026, 9, 30).date(), modalita_pagamento='MP01',
        )
        RigaFattura.objects.create(
            fattura=altra_fattura, articolo=self.articolo, quantita=1,
            prezzo=Decimal('4.500'), iva=22,
        )
        self.client.force_login(self.user)
        url = reverse('fattura-update', kwargs={'pk': self.fattura.pk})
        pagina = self.client.get(url)
        self.assertContains(pagina, 'Elimina tutte le righe della fattura')

        response = self.client.post(url, {'svuota_righe': '1', 'categoria': '0'}, follow=True)
        self.assertContains(response, 'Tutte le righe della fattura sono state eliminate')
        self.assertFalse(RigaFattura.objects.filter(fattura=self.fattura).exists())
        self.assertTrue(RigaFattura.objects.filter(fattura=altra_fattura).exists())
        self.fattura.refresh_from_db()
        self.assertEqual(self.fattura.totali['tot'], 0.0)

    def test_recupero_cls_storico_non_marcato_blocca_fino_a_svuotamento_esplicito(self):
        bolla = Bolla.objects.create(
            cliente=self.cliente, tipo_documento=self.tipo_cls,
            data=timezone.make_aware(datetime(2026, 9, 15, 10)),
        )
        RigaBolla.objects.create(bolla=bolla, articolo=self.articolo, quantita=2)
        storico = RigaFattura.objects.create(
            fattura=self.fattura_cls, articolo=self.articolo, quantita=1,
            prezzo=Decimal('4.500'), iva=22, descrizione_snapshot='Storico non marcato',
        )
        self.client.force_login(self.user)

        response = self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura_cls.pk}), {
            'recupera_totali': '1', 'mese': '9', 'anno': '2026', 'categoria': '0',
        }, follow=True)
        self.assertContains(response, 'righe non marcate')
        storico.refresh_from_db()
        self.assertIsNone(storico.recupero_cls_periodo)
        self.assertFalse(RigaFattura.objects.filter(
            fattura=self.fattura_cls, recupero_cls_periodo=datetime(2026, 9, 1).date(),
        ).exists())

        storico.delete()
        self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura_cls.pk}), {
            'recupera_totali': '1', 'mese': '9', 'anno': '2026', 'categoria': '0',
        })
        self.assertTrue(RigaFattura.objects.filter(
            fattura=self.fattura_cls, recupero_cls_periodo=datetime(2026, 9, 1).date(),
        ).exists())

    def test_recupero_cls_senza_sorgenti_puo_essere_ripetuto(self):
        self.client.force_login(self.user)
        self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura_cls.pk}), {
            'recupera_totali': '1', 'mese': '9', 'anno': '2026', 'categoria': '0',
        })
        self.assertFalse(RigaFattura.objects.filter(fattura=self.fattura_cls).exists())

        bolla = Bolla.objects.create(
            cliente=self.cliente, tipo_documento=self.tipo_cls,
            data=timezone.make_aware(datetime(2026, 9, 15, 10)),
        )
        RigaBolla.objects.create(bolla=bolla, articolo=self.articolo, quantita=2)
        self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura_cls.pk}), {
            'recupera_totali': '1', 'mese': '9', 'anno': '2026', 'categoria': '0',
        })
        self.assertEqual(RigaFattura.objects.filter(
            fattura=self.fattura_cls, recupero_cls_periodo=datetime(2026, 9, 1).date(),
        ).count(), 1)

    def test_recupero_cls_non_accetta_righe_residue_dopo_cancellazione_marcate(self):
        bolla = Bolla.objects.create(
            cliente=self.cliente, tipo_documento=self.tipo_cls,
            data=timezone.make_aware(datetime(2026, 9, 15, 10)),
        )
        RigaBolla.objects.create(bolla=bolla, articolo=self.articolo, quantita=2)
        self.client.force_login(self.user)
        url = reverse('fattura-update', kwargs={'pk': self.fattura_cls.pk})
        self.client.post(url, {'recupera_totali': '1', 'mese': '9', 'anno': '2026', 'categoria': '0'})
        RigaFattura.objects.filter(
            fattura=self.fattura_cls, recupero_cls_periodo=datetime(2026, 9, 1).date(),
        ).delete()
        manuale = RigaFattura.objects.create(
            fattura=self.fattura_cls, articolo=self.altro_articolo, quantita=1,
            prezzo=Decimal('3.000'), iva=10, descrizione_snapshot='Manuale da preservare',
        )
        response = self.client.post(url, {
            'recupera_totali': '1', 'mese': '9', 'anno': '2026', 'categoria': '0',
        }, follow=True)
        self.assertContains(response, 'righe non marcate')
        manuale.refresh_from_db()
        self.assertEqual(manuale.descrizione_snapshot, 'Manuale da preservare')
        self.assertFalse(RigaFattura.objects.filter(
            fattura=self.fattura_cls, recupero_cls_periodo=datetime(2026, 9, 1).date(),
        ).exists())

    def test_recupero_cls_richiede_id_configurato_esistente(self):
        self.client.force_login(self.user)
        url = reverse('fattura-update', kwargs={'pk': self.fattura_cls.pk})
        with override_settings(CLIENTE_CENTRALE_LATTE_ID=999999):
            pagina = self.client.get(url)
            self.assertNotContains(pagina, 'Recupera Totali')
            response = self.client.post(url, {
                'recupera_totali': '1', 'mese': '9', 'anno': '2026', 'categoria': '0',
            }, follow=True)
        self.assertContains(response, 'non è disponibile per questa fattura')

    def test_vincolo_cls_impedisce_doppia_riga_stesso_articolo_e_periodo(self):
        periodo = datetime(2026, 9, 1).date()
        RigaFattura.objects.create(
            fattura=self.fattura_cls, articolo=self.articolo, quantita=1,
            prezzo=Decimal('4.500'), iva=22, recupero_cls_periodo=periodo,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RigaFattura.objects.create(
                    fattura=self.fattura_cls, articolo=self.articolo, quantita=1,
                    prezzo=Decimal('4.500'), iva=22, recupero_cls_periodo=periodo,
                )

    def test_modifica_riga_personalizza_testo_senza_modificare_anagrafica(self):
        riga = self.riga()
        self.client.force_login(self.user)
        response = self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura.pk}), {
            'aggiorna_riga': '1', 'riga_id': riga.pk, 'articolo': self.altro_articolo.pk,
            'descrizione': 'Testo personalizzato\ncon seconda riga', 'categoria': '0',
        })

        self.assertEqual(response.status_code, 302)
        riga.refresh_from_db()
        self.altro_articolo.refresh_from_db()
        self.assertEqual(riga.articolo, self.altro_articolo)
        self.assertEqual(riga.descrizione_documento, 'Testo personalizzato\ncon seconda riga')
        self.assertEqual(self.altro_articolo.descrizione, 'Descrizione proposta nuova')
        self.assertEqual(riga.prezzo, Decimal('3.000'))
        self.assertEqual(riga.iva, 10)

    def test_modifica_quantita_prezzo_ricalcola_totali_e_preserva_snapshot(self):
        riga = self.riga(descrizione_snapshot='Descrizione personalizzata')
        self.client.force_login(self.user)
        response = self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura.pk}), {
            'aggiorna_importi': '1', 'riga_id': riga.pk, 'quantita': '3',
            'prezzo': '1.234', 'categoria': '0',
        })

        self.assertEqual(response.status_code, 302)
        riga.refresh_from_db()
        self.fattura.refresh_from_db()
        self.assertEqual(riga.quantita, 3)
        self.assertEqual(riga.prezzo, Decimal('1.234'))
        self.assertEqual(riga.imp, Decimal('3.702'))
        self.assertEqual(riga.descrizione_snapshot, 'Descrizione personalizzata')
        self.assertAlmostEqual(self.fattura.totali['22']['imp'], 3.702)

        with patch('bolle.utils.genera_fattura.genera_pdf_base64', return_value='pdf-base64'):
            xml = genera_fattura_xml(self.fattura)
        self.assertIn('<Quantita>3.00</Quantita>', xml)
        self.assertIn('<PrezzoUnitario>1.234</PrezzoUnitario>', xml)

    def test_modifica_importi_non_acquisisce_fallback_e_rifiuta_valori_non_validi(self):
        riga = self.riga()
        RigaFattura.objects.filter(pk=riga.pk).update(descrizione_snapshot=None)
        self.client.force_login(self.user)

        response = self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura.pk}), {
            'aggiorna_importi': '1', 'riga_id': riga.pk, 'quantita': '4',
            'prezzo': '0.000', 'categoria': '0',
        })
        self.assertEqual(response.status_code, 302)
        riga.refresh_from_db()
        self.assertEqual(riga.prezzo, Decimal('0.000'))
        self.assertIsNone(riga.descrizione_snapshot)

        response = self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura.pk}), {
            'aggiorna_importi': '1', 'riga_id': riga.pk, 'quantita': '0',
            'prezzo': '1.2345', 'categoria': '0',
        }, follow=True)
        self.assertContains(response, 'quantità intera valida')
        riga.refresh_from_db()
        self.assertEqual(riga.quantita, 4)
        self.assertEqual(riga.prezzo, Decimal('0.000'))
        self.assertIsNone(riga.descrizione_snapshot)

    def test_importi_dicitura_non_sono_modificabili(self):
        riga = self.riga(articolo=self.dicitura, quantita=1, prezzo=Decimal('0.000'))
        self.client.force_login(self.user)
        response = self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura.pk}), {
            'aggiorna_importi': '1', 'riga_id': riga.pk, 'quantita': '9',
            'prezzo': '99.000', 'categoria': '0',
        }, follow=True)

        self.assertContains(response, 'Diciture non hanno valori economici modificabili')
        riga.refresh_from_db()
        self.assertEqual(riga.quantita, 1)
        self.assertEqual(riga.prezzo, Decimal('0.000'))

    def test_cambio_articolo_in_dicitura_azzera_importi_e_totali(self):
        riga = self.riga(articolo=self.dicitura, quantita=3, prezzo=Decimal('0.000'))
        self.client.force_login(self.user)

        self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura.pk}), {
            'aggiorna_riga': '1', 'riga_id': riga.pk, 'articolo': self.articolo.pk,
            'descrizione': 'Articolo economico', 'categoria': '0',
        })
        self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura.pk}), {
            'aggiorna_importi': '1', 'riga_id': riga.pk, 'quantita': '3',
            'prezzo': '4.500', 'categoria': '0',
        })
        response = self.client.post(reverse('fattura-update', kwargs={'pk': self.fattura.pk}), {
            'aggiorna_riga': '1', 'riga_id': riga.pk, 'articolo': self.dicitura.pk,
            'descrizione': 'Testo dicitura personalizzato', 'categoria': '0',
        })

        self.assertEqual(response.status_code, 302)
        riga.refresh_from_db()
        self.fattura.refresh_from_db()
        self.assertEqual(riga.articolo, self.dicitura)
        self.assertEqual(riga.prezzo, Decimal('0.000'))
        self.assertEqual(riga.imp, Decimal('0.000'))
        self.assertEqual(riga.tot_iva, Decimal('0.000'))
        self.assertEqual(self.fattura.totali['tot'], 0.0)

    def test_dicitura_multilinea_nel_pdf_cumulativo_non_mostra_quantita_o_importi(self):
        from .views import disegna_fattura

        class CanvasRegistrante:
            def __init__(self):
                self.stringhe = []
                self.stringhe_destra = []

            def setTitle(self, *args): pass
            def drawImage(self, *args, **kwargs): pass
            def setFont(self, *args): pass
            def setLineWidth(self, *args): pass
            def line(self, *args): pass
            def showPage(self): pass

            def drawString(self, *args):
                self.stringhe.append(args[-1])

            def drawRightString(self, *args):
                self.stringhe_destra.append(args[-1])

        riga = self.riga(
            articolo=self.dicitura,
            quantita=2,
            prezzo=Decimal('0.000'),
            descrizione_snapshot='Prima riga\nSeconda riga',
        )
        self.fattura.concessionario.logo = 'logo-di-prova.png'
        canvas = CanvasRegistrante()

        disegna_fattura(canvas, self.fattura)

        self.assertIn('Prima riga', canvas.stringhe)
        self.assertIn('Seconda riga', canvas.stringhe)
        self.assertNotIn(str(riga.quantita), canvas.stringhe_destra)

