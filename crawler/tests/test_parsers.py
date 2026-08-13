from decimal import Decimal

from django.test import SimpleTestCase

from crawler.fipe import parsers
from crawler.tests.fake_client import load


class ParseMonthYearTests(SimpleTestCase):
    def test_parses_slash_format_with_trailing_space(self):
        self.assertEqual(parsers.parse_month_year("junho/2025 "), (6, 2025))

    def test_parses_de_format(self):
        self.assertEqual(parsers.parse_month_year("junho de 2025"), (6, 2025))

    def test_parses_accented_month(self):
        self.assertEqual(parsers.parse_month_year("março/2024"), (3, 2024))

    def test_rejects_unknown_period(self):
        with self.assertRaises(parsers.ParseError):
            parsers.parse_month_year("smarch/2024")


class ParseMoneyTests(SimpleTestCase):
    def test_parses_brazilian_currency(self):
        self.assertEqual(parsers.parse_money("R$ 22.431,00"), Decimal("22431.00"))

    def test_parses_value_above_one_million(self):
        self.assertEqual(parsers.parse_money("R$ 1.234.567,89"), Decimal("1234567.89"))

    def test_rejects_garbage(self):
        with self.assertRaises(parsers.ParseError):
            parsers.parse_money("indisponível")


class ParseCollectionsTests(SimpleTestCase):
    def test_parses_reference_tables(self):
        tables = parsers.parse_reference_tables(load("reference_tables.json"))
        self.assertEqual(len(tables), 3)
        self.assertEqual((tables[0].fipe_code, tables[0].month, tables[0].year), (322, 6, 2025))

    def test_parses_brands(self):
        brands = parsers.parse_brands(load("brands.json"))
        self.assertEqual([b.fipe_code for b in brands], [1, 21])
        self.assertEqual(brands[1].name, "Fiat")

    def test_parses_models(self):
        models = parsers.parse_models(load("models_21.json")["Modelos"])
        self.assertEqual(models[0].fipe_code, 4828)
        self.assertEqual(models[0].name, "Uno Mille 1.0")

    def test_takes_year_and_fuel_from_the_value_code(self):
        years = parsers.parse_model_years(load("model_years_4828.json"))
        self.assertEqual(years[0].fipe_year_code, "2013-1")
        self.assertEqual((years[0].year, years[0].fuel_type), (2013, 1))
        self.assertEqual((years[1].year, years[1].fuel_type), (2012, 2))

    def test_recognises_flex_and_electric_codes(self):
        payload = [
            {"Label": "2026 Flex", "Value": "2026-5"},
            {"Label": "2022 Elétrico", "Value": "2022-4"},
        ]
        years = parsers.parse_model_years(payload)

        self.assertEqual([y.fuel_type for y in years], [5, 4])

    def test_keeps_zero_km_sentinel_year(self):
        years = parsers.parse_model_years(load("model_years_4712.json"))
        self.assertEqual(years[0].year, 32000)


class ParseFuelTypeTests(SimpleTestCase):
    def test_maps_every_known_label(self):
        cases = {
            "1992 Gasolina": 1,
            "Álcool": 2,
            "2010 Diesel": 3,
            "2022 Elétrico": 4,
            "2026 Flex": 5,
            "2026 Híbrido": 6,
            "2016 Tetrafuel": 7,
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(parsers.parse_fuel_type(label), expected)

    def test_unknown_label_falls_back_to_the_default(self):
        self.assertEqual(parsers.parse_fuel_type("Hidrogênio", default=9), 9)


class ParseQuoteTests(SimpleTestCase):
    def test_parses_quote(self):
        quote = parsers.parse_quote(load("price.json"))
        self.assertEqual(quote.value, Decimal("22431.00"))
        self.assertEqual(quote.fipe_code, "001267-1")
        self.assertEqual(quote.brand_name, "Fiat")
        self.assertEqual(quote.year, 2013)
        self.assertEqual(quote.fuel_type, 1)

    def test_rejects_payload_missing_a_field(self):
        payload = load("price.json")
        del payload["CodigoFipe"]
        with self.assertRaises(parsers.ParseError):
            parsers.parse_quote(payload)
