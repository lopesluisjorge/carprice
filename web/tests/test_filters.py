from decimal import Decimal

from django.http import QueryDict
from django.test import SimpleTestCase

from web.filters import SearchFilters


def parse(querystring):
    return SearchFilters.from_query(QueryDict(querystring))


class ParsingTests(SimpleTestCase):
    def test_reads_every_field(self):
        filters = parse("q=corsa&fuel=5&fuel=6&year_op=lte&year=2015&page=3")
        self.assertEqual(filters.term, "corsa")
        self.assertEqual(filters.fuels, (5, 6))
        self.assertEqual(filters.year_op, "lte")
        self.assertEqual(filters.year, 2015)
        self.assertEqual(filters.page, 3)

    def test_empty_query_is_the_default(self):
        filters = parse("")
        self.assertEqual(filters, SearchFilters())
        self.assertTrue(filters.is_empty)

    def test_unknown_operator_falls_back_to_the_default(self):
        self.assertEqual(parse("year_op=drop&year=2015").year_op, "gte")

    def test_non_numeric_year_disables_the_year_filter(self):
        self.assertIsNone(parse("year=ontem&year_op=lte").year)

    def test_non_numeric_fuel_is_dropped_and_repeats_collapse(self):
        self.assertEqual(parse("fuel=5&fuel=x&fuel=5&fuel=6").fuels, (5, 6))

    def test_bad_page_becomes_the_first(self):
        self.assertEqual(parse("page=abc").page, 1)
        self.assertEqual(parse("page=-4").page, 1)

    def test_reads_the_brand_price_and_sort_fields(self):
        filters = parse("brand=1-21&price_op=gte&price=50000&sort=price_desc")
        self.assertEqual(filters.brand, "1-21")
        self.assertEqual(filters.price_op, "gte")
        self.assertEqual(filters.price, 50000)
        self.assertEqual(filters.sort, "price_desc")

    def test_price_defaults_to_at_most(self):
        # Ano se procura "a partir de"; preço, por teto de orçamento.
        self.assertEqual(parse("").price_op, "lte")

    def test_unknown_price_operator_falls_back_to_the_default(self):
        # "eq" existe para ano e não para preço.
        self.assertEqual(parse("price_op=eq&price=50000").price_op, "lte")

    def test_non_positive_or_non_numeric_price_disables_the_filter(self):
        for querystring in ["price=barato", "price=-5", "price=0"]:
            with self.subTest(querystring=querystring):
                self.assertIsNone(parse(querystring).price)

    def test_a_price_outside_the_steps_is_honoured(self):
        # O CLAUDE.md promete que a URL volta igual numa aba nova.
        self.assertEqual(parse("price=43500").price, 43500)

    def test_reads_the_engine_field(self):
        self.assertEqual(parse("engine=1.4").engine, Decimal("1.4"))
        self.assertEqual(parse("engine=-1").engine, Decimal("-1"))
        self.assertFalse(parse("engine=1.4").is_empty)

    def test_junk_infinity_and_oversized_engines_disable_the_filter(self):
        # Decimal() accepts "NaN" and "Infinity", and the column is numeric(3,1).
        for querystring in ["engine=motor", "engine=NaN", "engine=Infinity", "engine=1E99"]:
            with self.subTest(querystring=querystring):
                self.assertIsNone(parse(querystring).engine)

    def test_unknown_sort_is_dropped(self):
        self.assertEqual(parse("sort=drop").sort, "")

    def test_sorting_alone_still_counts_as_an_empty_filter(self):
        # Ordenar não é filtrar: a tela vazia continua dizendo "colete dados".
        self.assertTrue(parse("sort=price_asc").is_empty)


class QuerystringTests(SimpleTestCase):
    def test_round_trip(self):
        original = (
            "q=corsa&brand=1-21&fuel=5&fuel=6&engine=1.4&year_op=lte&year=2015"
            "&price_op=gte&price=50000&sort=price_asc&page=3"
        )
        self.assertEqual(parse(parse(original).querystring()), parse(original))

    def test_empty_filters_produce_an_empty_querystring(self):
        self.assertEqual(SearchFilters().querystring(), "")

    def test_override_replaces_one_field(self):
        filters = parse("q=corsa&page=3")
        self.assertEqual(filters.querystring(page=4), "q=corsa&page=4")

    def test_first_page_is_left_out_of_the_link(self):
        self.assertEqual(parse("q=corsa&page=3").querystring(page=1), "q=corsa")
