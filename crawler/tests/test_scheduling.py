from datetime import date

from django.test import SimpleTestCase

from crawler.models import ZERO_KM_YEAR
from crawler.services import scheduling


class PeriodsForTests(SimpleTestCase):
    today = date(2026, 8, 1)

    def test_recent_points_come_first(self):
        periods = scheduling.periods_for(2015, self.today)
        self.assertEqual(periods[:4], [(2026, 8), (2026, 5), (2026, 2), (2025, 8)])

    def test_yearly_step_stops_one_year_after_the_version(self):
        periods = scheduling.periods_for(2015, self.today)
        self.assertEqual(periods[-1], (2016, 8))
        self.assertEqual(len(periods), 13)

    def test_the_twelve_month_point_and_the_first_yearly_step_collapse(self):
        # Both are (2025, 8); the set must not offer it twice.
        self.assertEqual(scheduling.periods_for(2015, self.today).count((2025, 8)), 1)

    def test_zero_km_has_no_yearly_step(self):
        self.assertEqual(
            scheduling.periods_for(ZERO_KM_YEAR, self.today),
            [(2026, 8), (2026, 5), (2026, 2), (2025, 8)],
        )

    def test_a_current_year_version_has_no_yearly_step(self):
        self.assertEqual(len(scheduling.periods_for(2026, self.today)), 4)

    def test_crossing_the_year_boundary(self):
        # January: three months back lands in October of the previous year.
        self.assertEqual(
            scheduling.periods_for(2024, date(2026, 1, 1)),
            [(2026, 1), (2025, 10), (2025, 7), (2025, 1)],
        )

    def test_an_old_version_gets_the_full_yearly_ladder(self):
        periods = scheduling.periods_for(1996, self.today)
        self.assertEqual(periods[-1], (1997, 8))
        self.assertEqual(len(periods), 32)
