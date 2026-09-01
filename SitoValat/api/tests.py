from django.contrib.auth import get_user_model
from django.urls import reverse
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
        self.proprietario = Proprietario.objects.create(nome="Proprietario")
        self.cliente_a = self._cliente("Cliente A", self.concessionario_a, self.zona_a, self.tipo_a)
        self.cliente_a_seconda = self._cliente(
            "Cliente A seconda", self.concessionario_a, self.zona_a_seconda, self.tipo_a
        )
        self.cliente_b = self._cliente("Cliente B", self.concessionario_b, self.zona_b, self.tipo_b)
        self.bolla_a = Bolla.objects.create(cliente=self.cliente_a, tipo_documento=self.tipo_a)
        self.bolla_a_seconda = Bolla.objects.create(
            cliente=self.cliente_a_seconda, tipo_documento=self.tipo_a
        )
        self.bolla_b = Bolla.objects.create(cliente=self.cliente_b, tipo_documento=self.tipo_b)
        categoria = Categoria.objects.create(nome="Test")
        self.articolo = Articolo.objects.create(
            nome="600001", descrizione="Articolo test", categoria=categoria
        )
        ArticoliConcessi.objects.create(proprietario=self.proprietario, articolo=self.articolo)

    def _cliente(self, nome, concessionario, zona, tipo_documento):
        return Cliente.objects.create(
            nome=nome,
            concessionario=concessionario,
            zona=zona,
            tipo_documento_predefinito=tipo_documento,
            proprietario=self.proprietario,
            piva=f"IT{Cliente.objects.count() + 100:011d}",
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
