from datetime import date
from datetime import timedelta

from django.test import SimpleTestCase
from django.test import TestCase
from django.utils import timezone

from crawler.models import ZERO_KM_YEAR
from crawler.models import Brand
from crawler.models import CollectionRequest
from crawler.models import CollectionStatus
from crawler.models import VehicleModel
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


def build_models(count):
    brand, _ = Brand.objects.get_or_create(fipe_code=21, defaults={"name": "Fiat"})
    return [
        VehicleModel.objects.create(brand=brand, fipe_code=index, name=f"Modelo {index}")
        for index in range(1, count + 1)
    ]


class RequestCollectionTests(TestCase):
    def setUp(self):
        self.models = build_models(4)
        self.ids = [model.pk for model in self.models]

    def test_creates_a_request_with_one_item_per_model_in_rank_order(self):
        request = scheduling.request_collection("palio", self.ids)

        self.assertEqual(request.term, "palio")
        self.assertEqual(request.status, CollectionStatus.PENDING)
        self.assertEqual(
            list(request.items.values_list("vehicle_model_id", "rank")),
            [(self.ids[0], 0), (self.ids[1], 1), (self.ids[2], 2), (self.ids[3], 3)],
        )

    def test_an_empty_search_schedules_nothing(self):
        self.assertIsNone(scheduling.request_collection("", []))
        self.assertIsNone(scheduling.request_collection("palio", []))
        self.assertEqual(CollectionRequest.objects.count(), 0)

    def test_a_fully_covered_search_reuses_the_existing_request(self):
        first = scheduling.request_collection("palio", self.ids)
        again = scheduling.request_collection("palio fire", self.ids[:2])

        self.assertEqual(again.pk, first.pk)
        self.assertEqual(CollectionRequest.objects.count(), 1)

    def test_a_partly_covered_search_schedules_only_the_rest(self):
        scheduling.request_collection("palio", self.ids[:2])
        second = scheduling.request_collection("fiat", self.ids)

        self.assertEqual(CollectionRequest.objects.count(), 2)
        self.assertEqual(
            list(second.items.values_list("vehicle_model_id", flat=True)), self.ids[2:]
        )

    def test_coverage_expires_after_the_window(self):
        first = scheduling.request_collection("palio", self.ids)
        CollectionRequest.objects.filter(pk=first.pk).update(
            created_at=timezone.now() - timedelta(hours=49)
        )

        second = scheduling.request_collection("palio", self.ids)

        self.assertNotEqual(second.pk, first.pk)
        self.assertEqual(second.items.count(), 4)

    def test_a_pending_request_still_counts_as_covering(self):
        # Otherwise a slow queue would schedule the same models over and over.
        first = scheduling.request_collection("palio", self.ids)
        self.assertEqual(first.status, CollectionStatus.PENDING)

        self.assertEqual(scheduling.request_collection("palio", self.ids).pk, first.pk)

    def test_a_finished_request_still_covers_inside_the_window(self):
        first = scheduling.request_collection("palio", self.ids)
        CollectionRequest.objects.filter(pk=first.pk).update(
            status=CollectionStatus.COMPLETED
        )

        self.assertEqual(scheduling.request_collection("palio", self.ids).pk, first.pk)
