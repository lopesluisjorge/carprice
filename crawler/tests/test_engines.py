from decimal import Decimal

from django.test import SimpleTestCase

from crawler import engines
from crawler.models import FuelType


class ParseDisplacementTests(SimpleTestCase):
    def test_reads_the_liters_out_of_the_name(self):
        self.assertEqual(
            engines.parse_displacement("UNO EVOLUTION 1.4 Fire Flex 8V 5p"), Decimal("1.4")
        )

    def test_reads_the_old_cc_notation(self):
        self.assertEqual(engines.parse_displacement("Gol 1000 Mi 16V 2p Turbo"), Decimal("1.0"))
        self.assertEqual(engines.parse_displacement("Logus GLSi / GLS 2000"), Decimal("2.0"))
        self.assertEqual(engines.parse_displacement("Gol 1000i Plus 2p"), Decimal("1.0"))

    def test_a_name_listing_several_keeps_the_first(self):
        self.assertEqual(
            engines.parse_displacement("Premio CS 1.6/ 1.5/ 1.3 2p"), Decimal("1.6")
        )

    def test_a_dot_to_the_left_is_not_part_of_the_number(self):
        self.assertEqual(
            engines.parse_displacement("430i Cab. Sport Limited Ed.2.0 TB 2p"), Decimal("2.0")
        )

    def test_does_not_carve_a_displacement_out_of_a_longer_number(self):
        # The truck "9.170" is a model name; there is no 9.1 engine in it.
        self.assertIsNone(engines.parse_displacement("Delivery 9.170 4x2"))

    def test_a_model_name_that_ends_in_hundreds_is_not_an_engine(self):
        self.assertIsNone(engines.parse_displacement("F-4000 Diesel"))

    def test_a_name_without_a_displacement_reads_as_nothing(self):
        self.assertIsNone(engines.parse_displacement("323iA Confort"))


class ClassifyTests(SimpleTestCase):
    def test_a_combustion_model_gets_its_displacement(self):
        self.assertEqual(
            engines.classify("ARGO DRIVE 1.3 8V Flex", [FuelType.FLEX]), Decimal("1.3")
        )

    def test_an_electric_is_negative_one_even_when_the_name_is_silent(self):
        # FIPE marks most of them with "(Elétrico)", but not "Dolphin Mini GL".
        self.assertEqual(
            engines.classify("Dolphin Mini GL", [FuelType.ELECTRIC]), engines.ELECTRIC
        )

    def test_an_electric_with_no_stored_years_is_read_off_the_name(self):
        self.assertEqual(engines.classify("Seal (Elétrico)", []), engines.ELECTRIC)

    def test_a_hybrid_that_names_a_displacement_is_that_displacement(self):
        self.assertEqual(
            engines.classify("King GL 1.5 16V Aut. (Hibrido)", [FuelType.HYBRID]), Decimal("1.5")
        )

    def test_a_hybrid_with_no_displacement_is_negative_two(self):
        self.assertEqual(
            engines.classify("PULSE AUDACE Turbo 200 Aut. (Hibrído)", [FuelType.HYBRID]),
            engines.HYBRID,
        )

    def test_combustion_without_a_displacement_in_the_name_is_zero(self):
        self.assertEqual(engines.classify("323iA Confort", [FuelType.GASOLINE]), engines.UNKNOWN)


class DescribeTests(SimpleTestCase):
    def test_a_displacement_describes_itself(self):
        self.assertEqual(engines.describe(Decimal("1.0")), "1.0")
        self.assertEqual(engines.describe(Decimal("1")), "1.0")

    def test_the_negative_codes_have_names(self):
        self.assertEqual(engines.describe(engines.ELECTRIC), "Elétrico")
        self.assertEqual(engines.describe(engines.HYBRID), "Híbrido")
        self.assertEqual(engines.describe(engines.UNKNOWN), "Não informado")
