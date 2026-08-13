from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from crawler.models import Brand
from crawler.models import FuelType
from crawler.models import ModelYear
from crawler.models import PriceQuote
from crawler.models import ReferenceTable
from crawler.models import VehicleModel

from web import codes
from web import queries


def build_vehicle(brand_code=21, model_code=4712, year=2017, fuel=FuelType.FLEX):
    brand, _ = Brand.objects.get_or_create(fipe_code=brand_code, defaults={"name": "Fiat"})
    vehicle_model, _ = VehicleModel.objects.get_or_create(
        brand=brand, fipe_code=model_code, defaults={"name": "500 Cult 1.4"}
    )
    return ModelYear.objects.create(
        vehicle_model=vehicle_model,
        fipe_year_code=f"{year}-{int(fuel)}",
        year=year,
        fuel_type=fuel,
    )


def add_quote(model_year, year, month, value):
    reference_table, _ = ReferenceTable.objects.get_or_create(
        year=year, month=month, defaults={"fipe_code": year * 100 + month}
    )
    return PriceQuote.objects.create(
        model_year=model_year,
        reference_table=reference_table,
        value=Decimal(value),
        fipe_code="001124-0",
        fuel_type=model_year.fuel_type,
    )


class CodeTests(TestCase):
    def test_round_trip(self):
        model_year = build_vehicle()
        code = codes.encode(model_year)
        self.assertEqual(code, "1-21-4712-2017-5")
        self.assertEqual(codes.get(code), model_year)

    def test_malformed_codes_resolve_to_nothing(self):
        for code in ["", "abc", "1-21-4712", "1-21-4712-2017-5-9", "1-21-4712-2017-x"]:
            with self.subTest(code=code):
                self.assertIsNone(codes.get(code))

    def test_parse_list_drops_repeats_and_respects_the_limit(self):
        self.assertEqual(codes.parse_list("a, b ,a,c,d", limit=3), ["a", "b", "c"])


class VariationTests(TestCase):
    def test_uses_the_closest_month_at_or_before_the_target(self):
        model_year = build_vehicle()
        add_quote(model_year, 2025, 8, "100000.00")
        add_quote(model_year, 2026, 2, "90000.00")  # 6 months back
        add_quote(model_year, 2026, 8, "99000.00")
        quotes = queries.history(model_year)

        six = queries.variation(quotes, 6)
        self.assertEqual(six["previous_value"], Decimal("90000.00"))
        self.assertEqual(six["delta"], Decimal("9000.00"))
        self.assertAlmostEqual(six["percent"], 10.0)

        # No month exactly 3 back, so it falls back to the older one and says so.
        three = queries.variation(quotes, 3)
        self.assertEqual(three["reference_table"].month, 2)

    def test_no_older_quote_gives_no_variation(self):
        model_year = build_vehicle()
        add_quote(model_year, 2026, 8, "99000.00")
        self.assertIsNone(queries.variation(queries.history(model_year), 3))

    def test_windows_keep_their_label_when_empty(self):
        model_year = build_vehicle()
        add_quote(model_year, 2026, 8, "99000.00")
        variations = queries.variations(queries.history(model_year))
        self.assertEqual([entry["months"] for entry in variations], [3, 6, 12])
        self.assertNotIn("percent", variations[0])


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
