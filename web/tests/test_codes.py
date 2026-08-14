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
