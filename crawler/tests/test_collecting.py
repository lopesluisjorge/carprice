from datetime import date

from django.test import TestCase

from crawler.models import Brand
from crawler.models import CollectionRequest
from crawler.models import CollectionStatus
from crawler.models import ModelYear
from crawler.models import PriceQuote
from crawler.models import ReferenceTable
from crawler.models import VehicleModel
from crawler.services import collecting
from crawler.services import scheduling
from crawler.tests.fake_client import FakeFipeClient

# Pinned to the fixture's newest reference table. The periods a version needs
# are computed from "today", so a real date would ask for months the recorded
# catalogue does not have and every test would collect nothing.
TODAY = date(2025, 6, 1)


def build_model(fipe_code=4828, years=(2024,)):
    brand, _ = Brand.objects.get_or_create(fipe_code=21, defaults={"name": "Fiat"})
    model = VehicleModel.objects.create(
        brand=brand, fipe_code=fipe_code, name=f"Modelo {fipe_code}"
    )
    for year in years:
        ModelYear.objects.create(
            vehicle_model=model, fipe_year_code=f"{year}-1", year=year, fuel_type=1
        )
    return model


class ProcessRequestTests(TestCase):
    """The fixture offers three reference tables: 06, 05 and 04 of 2025."""

    def setUp(self):
        self.client_ = FakeFipeClient()

    def schedule(self, models):
        return scheduling.request_collection("teste", [m.pk for m in models])

    def test_collects_and_marks_the_request_completed(self):
        request = self.schedule([build_model(years=(2024,))])

        collecting.process_request(self.client_, request, budget=100, today=TODAY)

        request.refresh_from_db()
        self.assertEqual(request.status, CollectionStatus.COMPLETED)
        self.assertEqual(request.models_done, 1)
        self.assertGreater(request.quotes_created, 0)
        self.assertIsNotNone(request.finished_at)

    def test_skips_pairs_already_in_the_database(self):
        request = self.schedule([build_model(years=(2024,))])
        collecting.process_request(self.client_, request, budget=100, today=TODAY)
        collected = PriceQuote.objects.count()

        # Re-open the same request and run it again: every pair is already
        # stored, so it must cost zero calls to the price endpoint.
        request.items.update(status=CollectionStatus.PENDING)
        fresh = FakeFipeClient()
        collecting.process_request(fresh, request, budget=100, today=TODAY)

        self.assertEqual(PriceQuote.objects.count(), collected)
        self.assertEqual(fresh.count("price"), 0)

    def test_budget_leaves_the_request_partial_and_the_item_pending(self):
        request = self.schedule([build_model(years=(2020, 2021, 2022))])

        collecting.process_request(self.client_, request, budget=2, today=TODAY)

        request.refresh_from_db()
        self.assertEqual(request.status, CollectionStatus.PARTIAL)
        self.assertEqual(request.requests_spent, 2)
        self.assertEqual(request.items.filter(status=CollectionStatus.PENDING).count(), 1)

    def test_a_second_pass_resumes_where_it_stopped(self):
        request = self.schedule([build_model(years=(2020, 2021, 2022))])
        collecting.process_request(self.client_, request, budget=2, today=TODAY)
        partial = PriceQuote.objects.count()

        collecting.process_request(FakeFipeClient(), request, budget=100, today=TODAY)

        request.refresh_from_db()
        self.assertEqual(request.status, CollectionStatus.COMPLETED)
        self.assertGreater(PriceQuote.objects.count(), partial)

    def test_the_newest_month_of_every_version_comes_first(self):
        # Period-major order: an exhausted budget must leave a complete snapshot
        # of the current month, not the full history of one version.
        request = self.schedule([build_model(years=(2020, 2021, 2022))])

        collecting.process_request(self.client_, request, budget=3, today=TODAY)

        newest = ReferenceTable.objects.order_by("-year", "-month").first()
        self.assertEqual(PriceQuote.objects.filter(reference_table=newest).count(), 3)

    def test_a_period_fipe_does_not_have_is_skipped(self):
        # The fixture stops at 04/2025, so older yearly steps simply do not exist.
        request = self.schedule([build_model(years=(2015,))])

        collecting.process_request(self.client_, request, budget=100, today=TODAY)

        request.refresh_from_db()
        self.assertEqual(request.status, CollectionStatus.COMPLETED)

    def test_a_combination_fipe_cannot_price_is_counted_as_missing(self):
        request = self.schedule([build_model(years=(2024,))])

        collecting.process_request(
            FakeFipeClient(missing={"2024-1"}), request, budget=100, today=TODAY
        )

        request.refresh_from_db()
        self.assertGreater(request.quotes_missing, 0)
        self.assertEqual(request.quotes_created, 0)
        self.assertEqual(request.status, CollectionStatus.COMPLETED)
