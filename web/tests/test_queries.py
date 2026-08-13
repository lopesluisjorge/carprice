from decimal import Decimal

from django.test import TestCase

from web import queries
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
