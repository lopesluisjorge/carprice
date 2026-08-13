from decimal import Decimal

from django.test import TestCase

from crawler.models import (
    Brand,
    CrawlCheckpoint,
    CrawlRun,
    CrawlStatus,
    FuelType,
    ModelYear,
    PriceQuote,
    ReferenceTable,
    VehicleModel,
)
from crawler.services import sync
from crawler.tests.fake_client import FakeFipeClient


class SyncTests(TestCase):
    def setUp(self):
        self.client_ = FakeFipeClient()

    def test_populates_the_catalogue_and_quotes(self):
        run = sync.sync(self.client_)

        self.assertEqual(ReferenceTable.objects.get().fipe_code, 322)
        self.assertEqual(Brand.objects.count(), 2)
        self.assertEqual(VehicleModel.objects.count(), 2)
        self.assertEqual(ModelYear.objects.count(), 3)
        self.assertEqual(PriceQuote.objects.count(), 3)
        self.assertEqual(PriceQuote.objects.first().value, Decimal("22431.00"))
        self.assertEqual(run.status, CrawlStatus.COMPLETED)
        self.assertEqual(run.quotes_created, 3)
        self.assertIsNotNone(run.finished_at)

    def test_picks_the_most_recent_reference_table_by_default(self):
        sync.sync(self.client_)
        reference = ReferenceTable.objects.get()
        self.assertEqual((reference.month, reference.year), (6, 2025))

    def test_collects_a_past_reference_table(self):
        sync.sync(self.client_, period=(4, 2025))
        reference = ReferenceTable.objects.get()
        self.assertEqual((reference.fipe_code, reference.month), (320, 4))

    def test_rejects_a_reference_table_fipe_does_not_have(self):
        with self.assertRaises(ValueError):
            sync.sync(self.client_, period=(1, 1998))

    def test_running_twice_is_idempotent(self):
        sync.sync(self.client_)
        second = sync.sync(FakeFipeClient())

        self.assertEqual(PriceQuote.objects.count(), 3)
        self.assertEqual(Brand.objects.count(), 2)
        self.assertEqual(ModelYear.objects.count(), 3)
        self.assertEqual(second.quotes_created, 0)
        self.assertEqual(second.quotes_updated, 3)

    def test_a_new_reference_table_adds_history_instead_of_overwriting(self):
        sync.sync(self.client_, period=(5, 2025))
        sync.sync(FakeFipeClient(price_value="R$ 23.000,00"), period=(6, 2025))

        model_year = ModelYear.objects.get(fipe_year_code="2013-1")
        values = list(model_year.quotes.order_by("reference_table__month").values_list("value", flat=True))
        self.assertEqual(values, [Decimal("22431.00"), Decimal("23000.00")])

    def test_records_the_fuel_type_on_the_quote(self):
        sync.sync(self.client_)

        quote = PriceQuote.objects.get(model_year__fipe_year_code="2013-1")
        self.assertEqual(quote.fuel_type, FuelType.GASOLINE)

    def test_quote_fuel_comes_from_the_price_payload(self):
        """The payload wins: it is what FIPE actually priced."""

        class EthanolClient(FakeFipeClient):
            def price(self, *args, **kwargs):
                payload = super().price(*args, **kwargs)
                payload["Combustivel"] = "Álcool"
                return payload

        sync.sync(EthanolClient())

        self.assertEqual(PriceQuote.objects.count(), 3)
        self.assertTrue(all(q.fuel_type == FuelType.ETHANOL for q in PriceQuote.objects.all()))

    def test_fuel_type_survives_a_second_run(self):
        class DieselClient(FakeFipeClient):
            def price(self, *args, **kwargs):
                payload = super().price(*args, **kwargs)
                payload["Combustivel"] = "Diesel"
                return payload

        sync.sync(DieselClient())
        sync.sync(DieselClient())

        self.assertEqual(PriceQuote.objects.count(), 3)
        self.assertTrue(all(q.fuel_type == FuelType.DIESEL for q in PriceQuote.objects.all()))

    def test_skips_year_fuel_combinations_fipe_cannot_price(self):
        run = sync.sync(FakeFipeClient(missing={"2012-2"}))

        self.assertEqual(PriceQuote.objects.count(), 2)
        self.assertEqual(ModelYear.objects.count(), 3)
        self.assertEqual(run.quotes_created, 2)
        self.assertEqual(run.status, CrawlStatus.COMPLETED)

    def test_brand_filter_produces_a_partial_run(self):
        run = sync.sync(self.client_, brand_codes=[21])

        self.assertEqual(Brand.objects.count(), 1)
        self.assertEqual(run.status, CrawlStatus.RUNNING)

    def test_limit_stops_the_sweep(self):
        run = sync.sync(self.client_, limit=1)

        self.assertEqual(PriceQuote.objects.count(), 1)
        self.assertEqual(run.status, CrawlStatus.RUNNING)

    def test_dry_run_writes_nothing(self):
        sync.sync(self.client_, dry_run=True)

        self.assertEqual(PriceQuote.objects.count(), 0)
        self.assertEqual(Brand.objects.count(), 0)
        self.assertEqual(CrawlRun.objects.count(), 0)

    def test_dry_run_still_queries_fipe(self):
        sync.sync(self.client_, dry_run=True)
        self.assertEqual(self.client_.count("price"), 3)


class SyncBrandsTests(TestCase):
    def test_saves_only_the_brands(self):
        created, updated = sync.sync_brands(FakeFipeClient())

        self.assertEqual((created, updated), (2, 0))
        self.assertEqual(Brand.objects.count(), 2)
        self.assertEqual(VehicleModel.objects.count(), 0)
        self.assertEqual(PriceQuote.objects.count(), 0)

    def test_does_not_touch_the_model_or_price_endpoints(self):
        client = FakeFipeClient()
        sync.sync_brands(client)

        self.assertEqual(client.count("models"), 0)
        self.assertEqual(client.count("price"), 0)

    def test_does_not_create_a_crawl_run_or_checkpoints(self):
        sync.sync_brands(FakeFipeClient())

        self.assertEqual(CrawlRun.objects.count(), 0)
        self.assertEqual(CrawlCheckpoint.objects.count(), 0)

    def test_is_idempotent(self):
        sync.sync_brands(FakeFipeClient())
        created, updated = sync.sync_brands(FakeFipeClient())

        self.assertEqual((created, updated), (0, 2))
        self.assertEqual(Brand.objects.count(), 2)

    def test_corrects_a_renamed_brand(self):
        sync.sync_brands(FakeFipeClient())
        Brand.objects.filter(fipe_code=21).update(name="Fiaat")

        sync.sync_brands(FakeFipeClient())
        self.assertEqual(Brand.objects.get(fipe_code=21).name, "Fiat")

    def test_dry_run_writes_nothing(self):
        sync.sync_brands(FakeFipeClient(), dry_run=True)
        self.assertEqual(Brand.objects.count(), 0)

    def test_accepts_a_past_reference_table(self):
        sync.sync_brands(FakeFipeClient(), period=(4, 2025))
        self.assertEqual(ReferenceTable.objects.get().fipe_code, 320)

    def test_a_later_full_crawl_still_collects_those_brands(self):
        sync.sync_brands(FakeFipeClient())
        run = sync.sync(FakeFipeClient())

        self.assertEqual(Brand.objects.count(), 2)
        self.assertEqual(PriceQuote.objects.count(), 3)
        self.assertEqual(run.status, CrawlStatus.COMPLETED)


class SyncModelsTests(TestCase):
    def test_populates_models_and_years_without_prices(self):
        sync.sync_models(FakeFipeClient(), brand_codes=[21])

        self.assertEqual(VehicleModel.objects.count(), 2)
        self.assertEqual(ModelYear.objects.count(), 3)
        self.assertEqual(PriceQuote.objects.count(), 0)

    def test_does_not_touch_the_price_endpoint(self):
        client = FakeFipeClient()
        sync.sync_models(client, brand_codes=[21])

        self.assertEqual(client.count("models"), 1)
        self.assertEqual(client.count("model_years"), 2)
        self.assertEqual(client.count("price"), 0)

    def test_does_not_create_a_crawl_run_or_checkpoints(self):
        sync.sync_models(FakeFipeClient(), brand_codes=[21])

        self.assertEqual(CrawlRun.objects.count(), 0)
        self.assertEqual(CrawlCheckpoint.objects.count(), 0)

    def test_reports_created_then_updated_counts(self):
        first = sync.sync_models(FakeFipeClient(), brand_codes=[21])
        self.assertEqual((first.models_created, first.models_updated), (2, 0))
        self.assertEqual((first.years_created, first.years_updated), (3, 0))

        second = sync.sync_models(FakeFipeClient(), brand_codes=[21])
        self.assertEqual((second.models_created, second.models_updated), (0, 2))
        self.assertEqual((second.years_created, second.years_updated), (0, 3))

    def test_corrects_a_renamed_model(self):
        sync.sync_models(FakeFipeClient(), brand_codes=[21])
        VehicleModel.objects.filter(fipe_code=4712).update(name="Palio errado")

        sync.sync_models(FakeFipeClient(), brand_codes=[21])
        self.assertEqual(VehicleModel.objects.get(fipe_code=4712).name, "Palio EX 1.0")

    def test_walks_all_brands_by_default(self):
        client = FakeFipeClient()
        sync.sync_models(client)

        self.assertEqual(Brand.objects.count(), 2)
        # Both brands are asked for their models; only Fiat (21) returns any.
        self.assertEqual(client.count("models"), 2)
        self.assertEqual(VehicleModel.objects.count(), 2)

    def test_dry_run_writes_nothing(self):
        sync.sync_models(FakeFipeClient(), brand_codes=[21], dry_run=True)

        self.assertEqual(VehicleModel.objects.count(), 0)
        self.assertEqual(ModelYear.objects.count(), 0)
        self.assertEqual(Brand.objects.count(), 0)

    def test_a_later_full_crawl_still_collects_prices(self):
        sync.sync_models(FakeFipeClient(), brand_codes=[21])
        run = sync.sync(FakeFipeClient())

        self.assertEqual(PriceQuote.objects.count(), 3)
        self.assertEqual(run.status, CrawlStatus.COMPLETED)

    def test_tracks_progress_state(self):
        state = sync.CrawlProgress()
        sync.sync_models(FakeFipeClient(), brand_codes=[21], progress_state=state)

        self.assertEqual(state.brands_total, 1)
        self.assertEqual(state.brands_done, 1)
        self.assertEqual(state.models_total, 2)
        self.assertEqual(state.models_done, 2)


class ProgressTests(TestCase):
    def test_summary_is_explicit_before_anything_starts(self):
        self.assertEqual(sync.CrawlProgress().summary(), "sem progresso ainda")

    def test_tracks_models_and_quotes_to_completion(self):
        state = sync.CrawlProgress()
        sync.sync(FakeFipeClient(), progress_state=state)

        self.assertEqual(state.brands_total, 2)
        self.assertEqual(state.brands_done, 2)
        self.assertEqual(state.models_total, 2)
        self.assertEqual(state.models_done, 2)
        self.assertEqual(state.models_left, 0)
        self.assertEqual(state.quotes, 3)

    def test_summary_counts_existing_processed_and_remaining_models(self):
        state = sync.CrawlProgress()
        snapshots = []

        class WatchingClient(FakeFipeClient):
            def price(self, *args, **kwargs):
                snapshots.append(state.summary())
                return super().price(*args, **kwargs)

        sync.sync(WatchingClient(), progress_state=state)

        # Fiat has two models: the first quote sees none processed yet, the
        # last one sees the first model already finished.
        self.assertIn("2 existentes, 0 processados, 2 faltantes", snapshots[0])
        self.assertIn("2 existentes, 1 processados, 1 faltantes", snapshots[-1])
        self.assertIn("(Fiat)", snapshots[0])

    def test_summary_counts_quotes_collected_so_far(self):
        state = sync.CrawlProgress()
        snapshots = []

        class WatchingClient(FakeFipeClient):
            def price(self, *args, **kwargs):
                result = super().price(*args, **kwargs)
                snapshots.append(state.quotes)
                return result

        sync.sync(WatchingClient(), progress_state=state)

        # Sampled inside price(), so each snapshot precedes its own upsert.
        self.assertEqual(snapshots, [0, 1, 2])
        self.assertEqual(state.quotes, 3)

    def test_resumed_brands_still_count_as_done(self):
        with self.assertRaises(RuntimeError):
            sync.sync(ExplodingClient(fail_on_brand=21))

        state = sync.CrawlProgress()
        sync.sync(FakeFipeClient(), resume=True, progress_state=state)

        self.assertEqual(state.brands_done, 2)


class ResumeTests(TestCase):
    def test_failed_run_is_marked_and_keeps_its_checkpoints(self):
        client = ExplodingClient(fail_on_brand=21)

        with self.assertRaises(RuntimeError):
            sync.sync(client)

        run = CrawlRun.objects.get()
        self.assertEqual(run.status, CrawlStatus.FAILED)
        self.assertIn("RuntimeError", run.last_error)
        # Acura finished before the failure, so its checkpoint survived.
        self.assertTrue(CrawlCheckpoint.objects.get(brand__fipe_code=1).done)
        self.assertFalse(CrawlCheckpoint.objects.get(brand__fipe_code=21).done)

    def test_resume_reuses_the_run_and_skips_finished_brands(self):
        with self.assertRaises(RuntimeError):
            sync.sync(ExplodingClient(fail_on_brand=21))

        resumed = sync.sync(FakeFipeClient(), resume=True)

        self.assertEqual(CrawlRun.objects.count(), 1)
        self.assertEqual(resumed.status, CrawlStatus.COMPLETED)
        self.assertEqual(PriceQuote.objects.count(), 3)

    def test_resume_does_not_refetch_finished_brands(self):
        with self.assertRaises(RuntimeError):
            sync.sync(ExplodingClient(fail_on_brand=21))

        client = FakeFipeClient()
        sync.sync(client, resume=True)

        requested = [call[1] for call in client.calls if call[0] == "models"]
        self.assertEqual(requested, [21])

    def test_without_resume_a_second_attempt_starts_a_new_run(self):
        with self.assertRaises(RuntimeError):
            sync.sync(ExplodingClient(fail_on_brand=21))

        sync.sync(FakeFipeClient())
        self.assertEqual(CrawlRun.objects.count(), 2)


class ExplodingClient(FakeFipeClient):
    """Fails when asked for a given brand's models, simulating a mid-run crash."""

    def __init__(self, fail_on_brand, **kwargs):
        super().__init__(**kwargs)
        self.fail_on_brand = fail_on_brand

    def models(self, reference_code, vehicle_type, brand_code):
        if brand_code == self.fail_on_brand:
            raise RuntimeError("conexão perdida")
        return super().models(reference_code, vehicle_type, brand_code)
