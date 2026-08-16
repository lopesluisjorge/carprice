from django.test import TestCase

from web import codes
from web.tests.factories import build_vehicle


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


class ModelCodeTests(TestCase):
    def test_round_trip(self):
        model_year = build_vehicle()
        vehicle_model = model_year.vehicle_model
        code = codes.encode_model(vehicle_model)
        self.assertEqual(code, "1-21-4712")
        self.assertEqual(codes.get_model(code), vehicle_model)

    def test_a_version_code_is_not_a_model_code(self):
        model_year = build_vehicle()
        self.assertIsNone(codes.get_model(codes.encode(model_year)))
        self.assertIsNone(codes.get(codes.encode_model(model_year.vehicle_model)))

    def test_malformed_codes_resolve_to_nothing(self):
        for code in ["", "abc", "1-21", "1-21-4712-2017", "1-21-x"]:
            with self.subTest(code=code):
                self.assertIsNone(codes.get_model(code))


class BrandCodeTests(TestCase):
    def test_round_trip(self):
        model_year = build_vehicle()
        brand = model_year.vehicle_model.brand
        code = codes.encode_brand(brand)
        self.assertEqual(code, "1-21")
        self.assertEqual(codes.get_brand(code), brand)

    def test_the_other_codes_are_not_brand_codes(self):
        # A contagem de partes é o que impede um código de resolver como outro.
        model_year = build_vehicle()
        self.assertIsNone(codes.get_brand(codes.encode_model(model_year.vehicle_model)))
        self.assertIsNone(codes.get_brand(codes.encode(model_year)))

    def test_malformed_codes_resolve_to_nothing(self):
        for code in ["", "abc", "1", "1-21-4712", "1-x"]:
            with self.subTest(code=code):
                self.assertIsNone(codes.get_brand(code))


class HostileCodeTests(TestCase):
    """Codes that pass `isdigit()` and still blow up `int()`.

    Both used to be an uncaught ValueError, which is a 500 on three screens
    reachable without logging in. They resolve to nothing now, like any other
    malformed code.
    """

    # A superscript is a digit to str.isdigit() and not a digit to int(); a
    # string past 4300 digits is refused by CPython outright.
    HOSTILE = ["²", "³", "①", "9" * 5000]

    def test_a_version_code_survives_them(self):
        for part in self.HOSTILE:
            with self.subTest(part=part[:8]):
                self.assertIsNone(codes.get(f"1-21-{part}-2017-5"))
                self.assertIsNone(codes.get(f"1-{part}-4712-2017-5"))
                self.assertIsNone(codes.get(f"1-21-4712-{part}-5"))

    def test_a_model_code_survives_them(self):
        for part in self.HOSTILE:
            with self.subTest(part=part[:8]):
                self.assertIsNone(codes.get_model(f"1-21-{part}"))

    def test_a_brand_code_survives_them(self):
        for part in self.HOSTILE:
            with self.subTest(part=part[:8]):
                self.assertIsNone(codes.get_brand(f"1-{part}"))

    def test_non_ascii_digits_are_not_accepted(self):
        # int('٣') is 3, so this one never crashed — it silently accepted a
        # second spelling of every code, which is a worse kind of bug.
        self.assertIsNone(codes.get_brand("1-٢١"))
        self.assertIsNone(codes.get_model("1-21-٤٧١٢"))

    def test_a_real_code_is_still_accepted(self):
        model_year = build_vehicle()
        self.assertEqual(codes.get(codes.encode(model_year)), model_year)
