from decimal import Decimal

from django.test import TestCase

from crawler.models import FuelType
from crawler.models import ZERO_KM_YEAR

from web import queries
from web.filters import SearchFilters
from web.tests.factories import add_quote
from web.tests.factories import build_vehicle


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


class SearchModelsTests(TestCase):
    def setUp(self):
        self.uno = build_vehicle(model_code=1, year=2015, fuel=FuelType.FLEX)
        self.uno_gas = build_vehicle(model_code=1, year=2015, fuel=FuelType.GASOLINE)
        self.palio = build_vehicle(model_code=2, year=2010, fuel=FuelType.FLEX)
        add_quote(self.uno, 2026, 8, "40000.00")
        add_quote(self.uno_gas, 2026, 8, "38000.00")
        add_quote(self.palio, 2026, 8, "25000.00")

    def test_groups_versions_into_one_card_per_model(self):
        page = queries.search_models(SearchFilters())
        cards = {card["vehicle_model"].fipe_code: card for card in page}
        self.assertEqual(cards[1]["versions"], 2)
        self.assertEqual(cards[1]["min_value"], Decimal("38000.00"))
        self.assertEqual(cards[1]["max_value"], Decimal("40000.00"))

    def test_price_range_uses_only_the_newest_reference(self):
        # An older, cheaper month must not drag the range down.
        add_quote(self.uno, 2026, 7, "10000.00")
        page = queries.search_models(SearchFilters())
        card = next(c for c in page if c["vehicle_model"].fipe_code == 1)
        self.assertEqual(card["min_value"], Decimal("38000.00"))

    def test_version_count_respects_the_fuel_filter(self):
        page = queries.search_models(SearchFilters(fuels=(FuelType.FLEX,)))
        card = next(c for c in page if c["vehicle_model"].fipe_code == 1)
        self.assertEqual(card["versions"], 1)

    def test_year_operators(self):
        cases = {("gte", 2015): {1}, ("lte", 2010): {2}, ("eq", 2010): {2}}
        for (op, year), expected in cases.items():
            with self.subTest(op=op, year=year):
                page = queries.search_models(SearchFilters(year_op=op, year=year))
                self.assertEqual({c["vehicle_model"].fipe_code for c in page}, expected)

    def test_zero_km_counts_as_the_newest_year(self):
        zero = build_vehicle(model_code=3, year=ZERO_KM_YEAR, fuel=FuelType.FLEX)
        add_quote(zero, 2026, 8, "90000.00")

        def codes_for(op, year):
            return {
                card["vehicle_model"].fipe_code
                for card in queries.search_models(SearchFilters(year_op=op, year=year))
            }

        self.assertIn(3, codes_for("gte", 2015))
        self.assertNotIn(3, codes_for("lte", 2015))
        self.assertNotIn(3, codes_for("eq", 2015))
