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


class QuerystringTests(SimpleTestCase):
    def test_round_trip(self):
        original = "q=corsa&fuel=5&fuel=6&year_op=lte&year=2015&page=3"
        self.assertEqual(parse(parse(original).querystring()), parse(original))

    def test_empty_filters_produce_an_empty_querystring(self):
        self.assertEqual(SearchFilters().querystring(), "")

    def test_override_replaces_one_field(self):
        filters = parse("q=corsa&page=3")
        self.assertEqual(filters.querystring(page=4), "q=corsa&page=4")

    def test_first_page_is_left_out_of_the_link(self):
        self.assertEqual(parse("q=corsa&page=3").querystring(page=1), "q=corsa")
