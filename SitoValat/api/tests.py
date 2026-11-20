from django.contrib.auth import get_user_model
from datetime import datetime
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from bolle.models import (
    Articolo,
    ArticoliConcessi,
    Bolla,
    Categoria,
    Cliente,
    Concessionario,
    Proprietario,
    TipoDocumento,
    TipoDocCounter,
    Zona,
)


class ApiScopingTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.concessionario_a = Concessionario.objects.create(
            nome="Concessionario A", indirizzo="Via A", partita_iva="IT00000000001"
        )
        self.concessionario_b = Concessionario.objects.create(
            nome="Concessionario B", indirizzo="Via B", partita_iva="IT00000000002"
        )
        self.conc_user = user_model.objects.create_user(username="conc-a", password="test")
        self.concessionario_a.user = self.conc_user
        self.concessionario_a.save(update_fields=["user"])
        self.zone_user = user_model.objects.create_user(username="zona-a", password="test")
        self.unscoped_user = user_model.objects.create_user(username="nessuno", password="test")

        self.zona_a = Zona.objects.create(
            nome="Zona A", concessionario=self.concessionario_a, user=self.zone_user
        )
        self.zona_a_seconda = Zona.objects.create(
            nome="Zona A seconda",
            concessionario=self.concessionario_a,
            user=user_model.objects.create_user(username="zona-a-2", password="test"),
        )
        self.zona_b = Zona.objects.create(
            nome="Zona B",
            concessionario=self.concessionario_b,
            user=user_model.objects.create_user(username="zona-b", password="test"),
        )
        self.tipo_a = TipoDocumento.objects.create(nome="A", concessionario=self.concessionario_a)
        self.tipo_b = TipoDocumento.objects.create(nome="B", concessionario=self.concessionario_b)
        self.proprietario = Proprietario.objects.create(nome="Proprietario A")
        self.proprietario_b = Proprietario.objects.create(nome="Proprietario B")
        self.cliente_a = self._cliente("Cliente A", self.concessionario_a, self.zona_a, self.tipo_a)
        self.cliente_a_seconda = self._cliente(
            "Cliente A seconda", self.concessionario_a, self.zona_a_seconda, self.tipo_a
        )
        self.cliente_b = self._cliente(
            "Cliente B",
            self.concessionario_b,
            self.zona_b,
            self.tipo_b,
            proprietario=self.proprietario_b,
        )
        self.bolla_a = Bolla.objects.create(cliente=self.cliente_a, tipo_documento=self.tipo_a)
        self.bolla_a_seconda = Bolla.objects.create(
            cliente=self.cliente_a_seconda, tipo_documento=self.tipo_a
        )
        self.bolla_b = Bolla.objects.create(cliente=self.cliente_b, tipo_documento=self.tipo_b)
        categoria = Categoria.objects.create(nome="Test")
        self.articolo = Articolo.objects.create(
            nome="600001", descrizione="Articolo test", categoria=categoria
        )
        self.articolo_b = Articolo.objects.create(
            nome="600002", descrizione="Articolo concessionario B", categoria=categoria
        )
        ArticoliConcessi.objects.create(proprietario=self.proprietario, articolo=self.articolo)
        ArticoliConcessi.objects.create(proprietario=self.proprietario_b, articolo=self.articolo_b)

    def _cliente(self, nome, concessionario, zona, tipo_documento, proprietario=None):
        sequence = Cliente.objects.count() + 100
        return Cliente.objects.create(
            nome=nome,
            concessionario=concessionario,
            zona=zona,
            tipo_documento_predefinito=tipo_documento,
            proprietario=proprietario or self.proprietario,
            codice=f"CL{sequence}",
            citta="Salerno",
            piva=f"IT{sequence:011d}",
        )

    def test_endpoints_require_authentication(self):
        self.assertEqual(self.client.get(reverse("api_bolle_list")).status_code, 401)
        self.assertEqual(self.client.get("/api/customers/").status_code, 401)
        self.assertEqual(
            self.client.post(
                "/api/bolle/quick/",
                {"customer_id": self.cliente_a.pk, "raw_lines": "600001 1"},
                format="json",
            ).status_code,
            401,
        )

    def test_concessionario_can_only_list_and_read_its_bolle(self):
        self.client.force_authenticate(self.conc_user)

        response = self.client.get(reverse("api_bolle_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {b["id"] for b in response.data["results"]},
            {self.bolla_a.pk, self.bolla_a_seconda.pk},
        )
        self.assertEqual(self.client.get(reverse("api_bolle_detail", args=[self.bolla_a.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("api_bolle_detail", args=[self.bolla_b.pk])).status_code, 404)

    def test_zone_user_can_only_access_its_zone_bolle_and_customers(self):
        self.client.force_authenticate(self.zone_user)

        response = self.client.get(reverse("api_bolle_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({b["id"] for b in response.data["results"]}, {self.bolla_a.pk})
        customers = self.client.get("/api/customers/")
        self.assertEqual(customers.status_code, 200)
        self.assertEqual({c["id"] for c in customers.data["results"]}, {self.cliente_a.pk})
        self.assertEqual(self.client.get(reverse("api_bolle_detail", args=[self.bolla_a_seconda.pk])).status_code, 404)

    def test_unscoped_authenticated_user_has_no_document_or_customer_access(self):
        self.client.force_authenticate(self.unscoped_user)

        self.assertEqual(self.client.get(reverse("api_bolle_list")).data["results"], [])
        self.assertEqual(self.client.get("/api/customers/").data["results"], [])
        self.assertEqual(self.client.get(reverse("api_bolle_detail", args=[self.bolla_a.pk])).status_code, 404)

    def test_superuser_with_concessionario_is_scoped_to_that_concessionario(self):
        self.conc_user.is_superuser = True
        self.conc_user.save(update_fields=["is_superuser"])
        self.client.force_authenticate(self.conc_user)

        customers = self.client.get("/api/customers/")
        bolle = self.client.get(reverse("api_bolle_list"))

        self.assertEqual({item["id"] for item in customers.data["results"]}, {
            self.cliente_a.pk,
            self.cliente_a_seconda.pk,
        })
        self.assertEqual({item["id"] for item in bolle.data["results"]}, {
            self.bolla_a.pk,
            self.bolla_a_seconda.pk,
        })

    def test_quick_create_rejects_customer_outside_scope(self):
        self.client.force_authenticate(self.zone_user)
        initial_count = Bolla.objects.count()

        response = self.client.post(
            "/api/bolle/quick/",
            {"customer_id": self.cliente_b.pk, "raw_lines": "600001 1"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Bolla.objects.count(), initial_count)

    def test_quick_create_allows_customer_inside_scope(self):
        self.client.force_authenticate(self.zone_user)

        response = self.client.post(
            "/api/bolle/quick/",
            {"customer_id": self.cliente_a.pk, "raw_lines": "600001 2"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        bolla = Bolla.objects.get(pk=response.data["bolla_id"])
        self.assertEqual(bolla.cliente, self.cliente_a)
        self.assertEqual(bolla.righe.get().quantita, 2)

    def test_zone_user_can_modify_and_delete_its_own_bolla(self):
        self.client.force_authenticate(self.zone_user)
        bolla_da_eliminare = Bolla.objects.create(
            cliente=self.cliente_a,
            tipo_documento=self.tipo_a,
        )

        modify_response = self.client.post(
            reverse("api_modifica", args=[self.bolla_a.pk]),
            {"raw_lines": "600001 1"},
            format="json",
        )
        delete_response = self.client.delete(reverse("api_delete", args=[bolla_da_eliminare.pk]))

        self.assertEqual(modify_response.status_code, 200)
        self.assertEqual(delete_response.status_code, 204)
        self.assertFalse(Bolla.objects.filter(pk=bolla_da_eliminare.pk).exists())

    def test_modify_and_delete_reject_bolla_outside_scope(self):
        self.client.force_authenticate(self.zone_user)

        modify_response = self.client.post(
            reverse("api_modifica", args=[self.bolla_b.pk]),
            {"raw_lines": "600001 1"},
            format="json",
        )
        delete_response = self.client.delete(reverse("api_delete", args=[self.bolla_b.pk]))

        self.assertEqual(modify_response.status_code, 404)
        self.assertEqual(delete_response.status_code, 404)
        self.assertTrue(Bolla.objects.filter(pk=self.bolla_b.pk).exists())

    def test_customers_require_authentication(self):
        self.assertEqual(self.client.get("/api/customers/").status_code, 401)

    def test_customers_search_by_name_and_keep_existing_fields(self):
        self.client.force_authenticate(self.zone_user)

        response = self.client.get("/api/customers/", {"q": "Cliente A"})

        self.assertEqual(response.status_code, 200)
        customer = response.data["results"][0]
        self.assertEqual(customer["id"], self.cliente_a.pk)
        self.assertEqual(customer["nome"], self.cliente_a.nome)
        self.assertEqual(customer["via"], self.cliente_a.via)
        self.assertEqual(customer["codice"], self.cliente_a.codice)
        self.assertEqual(customer["citta"], "Salerno")
        self.assertEqual(customer["zona"], {"id": self.zona_a.pk, "nome": self.zona_a.nome})
        self.assertEqual(
            customer["concessionario"],
            {"id": self.concessionario_a.pk, "nome": self.concessionario_a.nome},
        )

    def test_customers_search_by_code_and_respect_scope(self):
        self.client.force_authenticate(self.conc_user)

        response = self.client.get("/api/customers/", {"q": self.cliente_a_seconda.codice})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data["results"]], [self.cliente_a_seconda.pk])
        self.assertNotIn(self.cliente_b.pk, [item["id"] for item in response.data["results"]])

    def test_unscoped_user_cannot_search_customers(self):
        self.client.force_authenticate(self.unscoped_user)

        response = self.client.get("/api/customers/", {"q": "Cliente"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"], [])

    def test_articles_require_authentication(self):
        self.assertEqual(self.client.get(reverse("api_articles_list")).status_code, 401)

    def test_articles_search_exact_code_before_other_matches(self):
        articolo_esatto = Articolo.objects.create(
            nome="600010", descrizione="Altro articolo", categoria=self.articolo.categoria
        )
        ArticoliConcessi.objects.create(proprietario=self.proprietario, articolo=articolo_esatto)
        self.client.force_authenticate(self.zone_user)

        response = self.client.get(reverse("api_articles_list"), {"q": "600010"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["id"], articolo_esatto.pk)
        self.assertEqual(response.data["results"][0]["codice"], "600010")

    def test_articles_search_by_prefix_and_description(self):
        articolo_prefisso = Articolo.objects.create(
            nome="600010", descrizione="Formaggio speciale", categoria=self.articolo.categoria
        )
        ArticoliConcessi.objects.create(proprietario=self.proprietario, articolo=articolo_prefisso)
        self.client.force_authenticate(self.zone_user)

        prefix_response = self.client.get(reverse("api_articles_list"), {"q": "6000"})
        description_response = self.client.get(reverse("api_articles_list"), {"q": "speciale"})

        self.assertIn(articolo_prefisso.pk, [item["id"] for item in prefix_response.data["results"]])
        self.assertEqual([item["id"] for item in description_response.data["results"]], [articolo_prefisso.pk])

    def test_articles_limit_and_user_scope(self):
        for code in ("600010", "600011", "600012"):
            articolo = Articolo.objects.create(nome=code, descrizione="Articolo", categoria=self.articolo.categoria)
            ArticoliConcessi.objects.create(proprietario=self.proprietario, articolo=articolo)
        self.client.force_authenticate(self.zone_user)

        response = self.client.get(reverse("api_articles_list"), {"limit": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 2)
        self.assertNotIn(self.articolo_b.pk, [item["id"] for item in response.data["results"]])

    def test_articles_filter_by_customer_and_reject_customer_outside_scope(self):
        self.client.force_authenticate(self.zone_user)

        allowed_response = self.client.get(
            reverse("api_articles_list"), {"customer_id": self.cliente_a.pk}
        )
        outside_response = self.client.get(
            reverse("api_articles_list"), {"customer_id": self.cliente_b.pk}
        )

        self.assertEqual(allowed_response.status_code, 200)
        self.assertEqual([item["id"] for item in allowed_response.data["results"]], [self.articolo.pk])
        self.assertEqual(outside_response.status_code, 404)


class QuickBollaOfflineSyncTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.conc = Concessionario.objects.create(
            nome="Va.Lat test", indirizzo="Via Test 1", partita_iva="IT00000000009"
        )
        self.user = user_model.objects.create_user(username="quick-offline", password="test")
        self.conc.user = self.user
        self.conc.save(update_fields=["user"])
        self.zona = Zona.objects.create(nome="Giro", concessionario=self.conc, user=user_model.objects.create_user(username="zona-offline", password="test"))
        self.prop = Proprietario.objects.create(nome="Proprietario", codice="PROP")
        self.tipo_cls = TipoDocumento.objects.create(nome="CLS", descrizione="DDT CLS", concessionario=self.conc)
        self.tipo_nt = TipoDocumento.objects.create(nome="NT", descrizione="DDT NT", concessionario=self.conc)
        self.cliente = Cliente.objects.create(
            nome="Cliente", concessionario=self.conc, zona=self.zona, proprietario=self.prop,
            tipo_documento_predefinito=self.tipo_cls, via="Via Cliente 1", cap="84000",
            citta="Salerno", piva="IT00000000010", codice="C001",
        )
        categoria = Categoria.objects.create(nome="Offline")
        self.articolo = Articolo.objects.create(nome="600100", descrizione="Latte test", categoria=categoria)
        ArticoliConcessi.objects.create(proprietario=self.prop, articolo=self.articolo)
        self.client.force_authenticate(self.user)

    def payload(self, *, emission_id, numero, lotto="LOT-TEST", text="ricevuta test"):
        return {
            "emission_id": emission_id,
            "customer_id": self.cliente.pk,
            "tipo_documento_id": self.tipo_cls.pk,
            "numero": numero,
            "anno_emissione": 2026,
            "data_emissione": "2026-06-10T08:30:00+02:00",
            "righe": [{"codice": self.articolo.nome, "quantita": 3, "lotto": lotto}],
            "printed_receipt": {"renderer": "ddt-receipt-v1", "width": 32, "text": text},
        }

    def offline_settings(self):
        return override_settings(QUICKBOLLA_OFFLINE_CONCESSIONARIO_ID=self.conc.pk)

    def test_sync_preserves_original_number_lot_and_receipt(self):
        with self.offline_settings():
            response = self.client.post(
                reverse("api_quickbolla_sync"),
                self.payload(emission_id="f9072a6f-3d76-4d6d-a9e3-1db2b4ba9d6c", numero=10),
                format="json",
            )
        self.assertEqual(response.status_code, 201)
        bolla = Bolla.objects.get(pk=response.data["bolla_id"])
        self.assertEqual(bolla.numero, 10)
        self.assertEqual(bolla.anno_emissione, 2026)
        self.assertEqual(bolla.righe.get().lotto, "LOT-TEST")
        self.assertEqual(bolla.receipt_text, "ricevuta test")
        self.assertEqual(TipoDocCounter.objects.get(tipo=self.tipo_cls, anno=2026).ultimo_numero, 10)
        receipt = self.client.get(reverse("api_receipt", args=[bolla.pk]))
        self.assertEqual(receipt.data["source"], "stored")
        self.assertEqual(receipt.data["text"], "ricevuta test")

    def test_identical_retry_returns_existing_bolla(self):
        payload = self.payload(emission_id="20e6f009-a6e8-4e18-9eec-502de0e6bf83", numero=10)
        with self.offline_settings():
            first = self.client.post(reverse("api_quickbolla_sync"), payload, format="json")
            retry = self.client.post(reverse("api_quickbolla_sync"), payload, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data["status"], "already_synced")
        self.assertEqual(retry.data["bolla_id"], first.data["bolla_id"])
        self.assertEqual(Bolla.objects.count(), 1)

    def test_conflict_with_browser_number_is_explicit(self):
        with self.offline_settings():
            browser_bolla = Bolla.objects.create(
                cliente=self.cliente, tipo_documento=self.tipo_cls,
                data=timezone.make_aware(datetime(2026, 6, 10, 9, 0)),
            )
            response = self.client.post(
                reverse("api_quickbolla_sync"),
                self.payload(emission_id="9ec3042f-b6de-40e7-8972-968d0d7c9695", numero=browser_bolla.numero),
                format="json",
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "number_conflict")
        self.assertEqual(response.data["existing_bolla_id"], browser_bolla.pk)

    def test_catalog_and_sync_resume_after_browser_emission(self):
        with self.offline_settings():
            self.client.post(
                reverse("api_quickbolla_sync"),
                self.payload(emission_id="02db3ff0-3381-4bf1-9141-77eaa7f282b2", numero=10),
                format="json",
            )
            browser_bolla = Bolla.objects.create(
                cliente=self.cliente, tipo_documento=self.tipo_cls,
                data=timezone.make_aware(datetime(2026, 6, 10, 10, 0)),
            )
            catalog = self.client.get(reverse("api_quickbolla_catalog"), {"year": 2026})
            response = self.client.post(
                reverse("api_quickbolla_sync"),
                self.payload(emission_id="3c6c3d31-1c6b-4900-ae99-94a4a0c5a792", numero=12),
                format="json",
            )
        self.assertEqual(browser_bolla.numero, 11)
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(catalog.data["types"][0]["next_number"], 12)
        self.assertEqual(catalog.data["articles"][0]["ultimo_lotto"], "---")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(TipoDocCounter.objects.get(tipo=self.tipo_cls, anno=2026).ultimo_numero, 12)

    def test_deleting_offline_type_does_not_decrement_counter(self):
        with self.offline_settings():
            created = self.client.post(
                reverse("api_quickbolla_sync"),
                self.payload(emission_id="c04a1788-aa0c-4a72-a555-84519c21c7c5", numero=10),
                format="json",
            )
            deleted = self.client.delete(reverse("api_delete", args=[created.data["bolla_id"]]))
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(TipoDocCounter.objects.get(tipo=self.tipo_cls, anno=2026).ultimo_numero, 10)
