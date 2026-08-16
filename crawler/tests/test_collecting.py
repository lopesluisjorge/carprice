import tempfile
from datetime import date
from datetime import timedelta
from pathlib import Path

from django.test import TestCase
from django.utils import timezone

from crawler.models import Brand
from crawler.models import CollectionRequest
from crawler.models import CollectionStatus
from crawler.models import ModelYear
from crawler.models import PriceQuote
from crawler.models import QuoteLookup
from crawler.models import QuoteLookupStatus
from crawler.models import ReferenceTable
from crawler.models import VehicleModel
from crawler.services import collecting
from crawler.services import scheduling
from crawler.services.collecting import queue_lock
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
        self.assertTrue(
            QuoteLookup.objects.filter(status=QuoteLookupStatus.NOT_FOUND).exists()
        )

    def test_a_refusal_is_not_asked_again_inside_the_recheck_window(self):
        request = self.schedule([build_model(years=(2024,))])
        refusing = FakeFipeClient(missing={"2024-1"})
        collecting.process_request(refusing, request, budget=100, today=TODAY)
        asked = refusing.count("price")

        request.items.update(status=CollectionStatus.PENDING)
        collecting.process_request(refusing, request, budget=100, today=TODAY)

        self.assertEqual(refusing.count("price"), asked)

    def test_the_refusal_expires_and_the_newest_month_is_asked_again(self):
        request = self.schedule([build_model(years=(2024,))])
        refusing = FakeFipeClient(missing={"2024-1"})
        collecting.process_request(refusing, request, budget=100, today=TODAY)
        asked = refusing.count("price")

        QuoteLookup.objects.update(
            checked_at=timezone.now() - collecting.NOT_FOUND_RECHECK - timedelta(days=1)
        )
        request.items.update(status=CollectionStatus.PENDING)
        collecting.process_request(refusing, request, budget=100, today=TODAY)

        self.assertGreater(refusing.count("price"), asked)

    def test_a_refusal_in_a_closed_month_never_expires(self):
        # Reached directly: no version's period ladder lands on an older table
        # in this fixture, and the rule is about the table, not the ladder.
        version = build_model(years=(2024,)).model_years.get()
        newest = ReferenceTable.objects.create(fipe_code=322, month=6, year=2025)
        closed = ReferenceTable.objects.create(fipe_code=321, month=5, year=2025)
        QuoteLookup.objects.create(
            model_year=version,
            reference_table=closed,
            status=QuoteLookupStatus.NOT_FOUND,
        )
        QuoteLookup.objects.update(checked_at=timezone.now() - timedelta(days=3650))

        self.assertTrue(collecting._settled(version, closed, newest))

    def test_a_tight_budget_no_longer_stalls_on_refusals(self):
        # Three versions FIPE refuses and room for two: the first pass must not
        # leave a request that re-spends the same budget on the same 404s.
        request = self.schedule([build_model(years=(2024, 2023, 2022))])
        refusing = FakeFipeClient(missing={"2024-1", "2023-1", "2022-1"})

        collecting.process_request(refusing, request, budget=2, today=TODAY)
        request.refresh_from_db()
        self.assertEqual(request.status, CollectionStatus.PARTIAL)

        collecting.process_request(refusing, request, budget=2, today=TODAY)
        request.refresh_from_db()
        self.assertEqual(request.status, CollectionStatus.COMPLETED)
        self.assertEqual(refusing.count("price"), 3)

    def test_the_worker_records_a_lookup_for_every_pair_it_asks_for(self):
        request = self.schedule([build_model(years=(2024,))])

        collecting.process_request(self.client_, request, budget=100, today=TODAY)

        request.refresh_from_db()
        self.assertEqual(QuoteLookup.objects.count(), request.quotes_created)
        self.assertEqual(
            QuoteLookup.objects.exclude(status=QuoteLookupStatus.CREATED).count(), 0
        )


class LockTests(TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "queue.lock"

    def test_a_second_holder_is_refused(self):
        with queue_lock(self.path):
            with self.assertRaises(collecting.QueueBusy):
                with queue_lock(self.path):
                    pass

    def test_the_lock_is_released_on_exit(self):
        with queue_lock(self.path):
            pass
        with queue_lock(self.path):
            pass  # must not raise


class ReclaimTests(TestCase):
    def test_a_request_left_running_is_reclaimed(self):
        request = scheduling.request_collection("teste", [build_model().pk])
        CollectionRequest.objects.filter(pk=request.pk).update(
            status=CollectionStatus.RUNNING
        )

        collecting.reclaim_stale_requests()

        request.refresh_from_db()
        self.assertEqual(request.status, CollectionStatus.PARTIAL)
        self.assertIn(request, collecting.pending_requests())
