from django.test import TestCase
from django.urls import reverse

from crawler.models import Brand

from web import codes
from web.tests.factories import add_quote
from web.tests.factories import build_vehicle


class SearchTests(TestCase):
    def setUp(self):
        self.model_year = build_vehicle()
        add_quote(self.model_year, 2026, 8, "99000.00")

    def test_home_lists_only_brands_with_quotes(self):
        Brand.objects.create(fipe_code=99, name="Sem coleta")
        response = self.client.get(reverse("web:home"))
        self.assertContains(response, "Fiat")
        self.assertNotContains(response, "Sem coleta")

    def test_model_fragment_carries_the_target_field(self):
        brand = self.model_year.vehicle_model.brand
        response = self.client.get(
            reverse("web:model_options"), {"brand": brand.pk, "field": "add"}
        )
        self.assertContains(response, "500 Cult 1.4")
        self.assertContains(response, "field=add")

    def test_year_fragment_offers_the_shareable_code(self):
        response = self.client.get(
            reverse("web:year_options"),
            {"model": self.model_year.vehicle_model.pk, "field": "add"},
        )
        self.assertContains(response, 'name="add"')
        self.assertContains(response, "1-21-4712-2017-5")

    def test_unknown_field_falls_back_to_the_search(self):
        response = self.client.get(
            reverse("web:year_options"),
            {"model": self.model_year.vehicle_model.pk, "field": "evil\" onload=x"},
        )
        self.assertContains(response, 'name="v"')


class DetailTests(TestCase):
    def setUp(self):
        self.model_year = build_vehicle()
        add_quote(self.model_year, 2026, 8, "99000.00")

    def test_shows_the_current_price(self):
        response = self.client.get(reverse("web:detail"), {"v": codes.encode(self.model_year)})
        self.assertContains(response, "R$ 99.000,00")

    def test_single_month_hides_the_chart(self):
        response = self.client.get(reverse("web:detail"), {"v": codes.encode(self.model_year)})
        self.assertNotContains(response, 'id="price-chart"')

    def test_second_month_shows_the_chart(self):
        add_quote(self.model_year, 2026, 7, "98000.00")
        response = self.client.get(reverse("web:detail"), {"v": codes.encode(self.model_year)})
        self.assertContains(response, 'id="price-chart"')

    def test_unknown_vehicle_returns_to_the_search(self):
        response = self.client.get(reverse("web:detail"), {"v": "1-21-4712-1900-1"})
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Veículo não encontrado", status_code=404)


class CompareTests(TestCase):
    def setUp(self):
        self.vehicles = []
        for index in range(5):
            model_year = build_vehicle(model_code=4712 + index, year=2017)
            add_quote(model_year, 2026, 8, f"{90000 + index}.00")
            self.vehicles.append(model_year)
        self.codes = [codes.encode(model_year) for model_year in self.vehicles]

    def test_add_appends_to_the_selection(self):
        response = self.client.get(
            reverse("web:compare"), {"v": self.codes[0], "add": self.codes[1]}
        )
        self.assertEqual(len(response.context["summaries"]), 2)
        self.assertEqual(response.context["selection"], ",".join(self.codes[:2]))

    def test_selection_is_capped(self):
        response = self.client.get(
            reverse("web:compare"), {"v": ",".join(self.codes[:4]), "add": self.codes[4]}
        )
        self.assertEqual(len(response.context["summaries"]), 4)
        self.assertContains(response, "no máximo 4 versões")

    def test_duplicates_are_ignored(self):
        response = self.client.get(
            reverse("web:compare"), {"v": self.codes[0], "add": self.codes[0]}
        )
        self.assertEqual(len(response.context["summaries"]), 1)

    def test_unknown_codes_are_dropped_from_the_selection(self):
        response = self.client.get(
            reverse("web:compare"), {"v": f"{self.codes[0]},1-99-99-1900-1"}
        )
        self.assertEqual(response.context["selection"], self.codes[0])

    def test_removal_link_excludes_only_that_vehicle(self):
        response = self.client.get(reverse("web:compare"), {"v": ",".join(self.codes[:3])})
        summaries = response.context["summaries"]
        self.assertEqual(summaries[1]["without_me"], f"{self.codes[0]},{self.codes[2]}")

    def test_empty_selection_renders(self):
        response = self.client.get(reverse("web:compare"))
        self.assertContains(response, "Nenhuma versão selecionada")
