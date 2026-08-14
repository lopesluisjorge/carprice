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


class AvailableFacetsTests(TestCase):
    """The sidebar options. ModelYear.Meta.ordering is ["-year"], which Django
    drags into the SELECT of a DISTINCT — making it distinct over the *pair*
    and repeating every fuel once per year it appears in."""

    def test_each_fuel_is_offered_once(self):
        build_vehicle(model_code=1, year=2015, fuel=FuelType.FLEX)
        build_vehicle(model_code=1, year=2016, fuel=FuelType.FLEX)
        build_vehicle(model_code=1, year=2017, fuel=FuelType.GASOLINE)
        self.assertEqual(queries.available_fuels(), [FuelType.GASOLINE, FuelType.FLEX])

    def test_each_year_is_offered_once(self):
        build_vehicle(model_code=1, year=2015, fuel=FuelType.FLEX)
        build_vehicle(model_code=1, year=2015, fuel=FuelType.GASOLINE)
        self.assertEqual(queries.available_years(), [2015])

    def test_zero_km_is_not_offered_as_a_year(self):
        build_vehicle(model_code=1, year=ZERO_KM_YEAR, fuel=FuelType.FLEX)
        self.assertEqual(queries.available_years(), [])

    def test_brands_are_offered_once_and_only_when_priced(self):
        # Mais estrito que os outros facets de propósito: entre ~100 marcas, uma
        # opção que não devolve nada vira lista de decepções.
        priced = build_vehicle(brand_code=21, model_code=1, year=2015)
        build_vehicle(brand_code=21, model_code=2, year=2016)
        build_vehicle(brand_code=13, model_code=3, year=2015, brand_name="Citroën")
        add_quote(priced, 2026, 8, "40000.00")

        self.assertEqual([brand.fipe_code for brand in queries.available_brands()], [21])


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

    def test_price_ceiling_keeps_a_model_with_one_version_in_range(self):
        page = queries.search_models(SearchFilters(price_op="lte", price=39000))
        self.assertEqual({c["vehicle_model"].fipe_code for c in page}, {1, 2})

    def test_the_card_shrinks_to_the_versions_that_matched(self):
        # Mesma semântica que combustível e ano já têm: o card mostra o que casou.
        page = queries.search_models(SearchFilters(price_op="lte", price=39000))
        card = next(c for c in page if c["vehicle_model"].fipe_code == 1)
        self.assertEqual(card["min_value"], Decimal("38000.00"))
        self.assertEqual(card["max_value"], Decimal("38000.00"))
        self.assertEqual(card["versions"], 1)

    def test_price_floor_drops_the_cheaper_model(self):
        page = queries.search_models(SearchFilters(price_op="gte", price=39000))
        self.assertEqual({c["vehicle_model"].fipe_code for c in page}, {1})

    def test_brand_filter_keeps_only_that_brand(self):
        other = build_vehicle(brand_code=13, model_code=9, year=2015, brand_name="Citroën")
        add_quote(other, 2026, 8, "50000.00")
        page = queries.search_models(SearchFilters(brand="1-13"))
        self.assertEqual({c["vehicle_model"].fipe_code for c in page}, {9})

    def test_a_malformed_brand_code_disables_the_filter(self):
        # Mesmo comportamento de um ano não-numérico: desliga o filtro em vez de
        # estreitar para nada por acidente.
        page = queries.search_models(SearchFilters(brand="lixo"))
        self.assertEqual({c["vehicle_model"].fipe_code for c in page}, {1, 2})

    def test_price_sorting_reads_each_end_of_the_range(self):
        # 28k–90k contra 30k–35k é o único par que denuncia a chave errada:
        # por min_value o primeiro vem antes, por max_value também — mas se as
        # direções trocarem de chave, a ordem inverte.
        for year, value in [(2015, "28000.00"), (2016, "90000.00")]:
            wide = build_vehicle(brand_code=13, model_code=7, year=year, brand_name="Citroën")
            add_quote(wide, 2026, 8, value)
        for year, value in [(2015, "30000.00"), (2016, "35000.00")]:
            narrow = build_vehicle(brand_code=13, model_code=8, year=year, brand_name="Citroën")
            add_quote(narrow, 2026, 8, value)

        def codes_for(sort):
            page = queries.search_models(SearchFilters(brand="1-13", sort=sort))
            return [card["vehicle_model"].fipe_code for card in page]

        # 28.000 antes de 30.000; por max_value daria [8, 7].
        self.assertEqual(codes_for("price_asc"), [7, 8])
        # 90.000 antes de 35.000; por min_value daria [8, 7].
        self.assertEqual(codes_for("price_desc"), [7, 8])
