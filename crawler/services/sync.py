"""Walk the FIPE tree and upsert it into the database.

Two guarantees the rest of the project relies on:

* idempotent — running the same reference table twice changes nothing;
* resumable — an interrupted run leaves committed checkpoints that ``--resume``
  picks up, so progress is written as it happens rather than in one big
  transaction at the end.
"""

import logging

from django.db import transaction
from django.utils import timezone

from crawler.fipe import FipeNotFound
from crawler.fipe import parsers
from crawler.models import (
    Brand,
    CrawlCheckpoint,
    CrawlRun,
    CrawlStatus,
    ModelYear,
    PriceQuote,
    ReferenceTable,
    VehicleModel,
    VehicleType,
)

logger = logging.getLogger(__name__)


class LimitReached(Exception):
    """Internal signal: the caller's ``--limit`` was hit."""


class CrawlProgress:
    """Live tally of where the sweep is, in models.

    Kept as a plain mutable object so the caller can hand the same instance to
    the client's ``on_wait`` hook: every pause — the per-minute quota or an
    HTTP 429 — then reports how much is done and how much is left.
    """

    def __init__(self):
        self.brand = ""
        self.brands_total = 0
        self.brands_done = 0
        self.models_total = 0
        self.models_done = 0
        self.quotes = 0

    @property
    def models_left(self):
        return max(self.models_total - self.models_done, 0)

    def start_brand(self, name, models_total):
        self.brand = name
        self.models_total = models_total
        self.models_done = 0

    def summary(self):
        if not self.brand:
            return "sem progresso ainda"
        return (
            f"marca {self.brands_done + 1}/{self.brands_total} ({self.brand}) · "
            f"modelos: {self.models_total} existentes, {self.models_done} processados, "
            f"{self.models_left} faltantes · {self.quotes} cotações"
        )


def resolve_reference_table(client, period=None):
    """Return the ReferenceTable row for ``period`` (``(month, year)``).

    ``None`` means the current table, which FIPE returns first.
    """
    tables = parsers.parse_reference_tables(client.reference_tables())
    if not tables:
        raise ValueError("a FIPE não devolveu nenhuma tabela de referência")

    if period is None:
        chosen = tables[0]
    else:
        month, year = period
        chosen = next((t for t in tables if t.month == month and t.year == year), None)
        if chosen is None:
            raise ValueError(f"tabela de referência {month:02d}/{year} não existe na FIPE")

    reference, _ = ReferenceTable.objects.get_or_create(
        fipe_code=chosen.fipe_code,
        defaults={"month": chosen.month, "year": chosen.year},
    )
    return reference


def start_run(reference, vehicle_type, resume=False):
    """Create a CrawlRun, or pick up the last unfinished one when resuming."""
    if resume:
        existing = (
            CrawlRun.objects.filter(
                reference_table=reference,
                vehicle_type=vehicle_type,
                status__in=[CrawlStatus.RUNNING, CrawlStatus.FAILED],
            )
            .order_by("-started_at")
            .first()
        )
        if existing:
            existing.status = CrawlStatus.RUNNING
            existing.last_error = ""
            existing.save(update_fields=["status", "last_error"])
            return existing
    return CrawlRun.objects.create(reference_table=reference, vehicle_type=vehicle_type)


def sync_brands(client, vehicle_type=VehicleType.CAR, period=None, dry_run=False, progress=None):
    """Upsert only the brand catalogue: no models, years or quotes.

    Two requests total. Deliberately does not create a CrawlRun — this is a
    catalogue refresh, not a price crawl, and inventing checkpoints here would
    make a later ``--resume`` skip brands whose prices were never collected.

    Returns ``(created, updated)``.
    """
    if not dry_run:
        return _sync_brands(client, vehicle_type, period, progress)

    report = progress or (lambda message: None)
    with transaction.atomic():
        result = _sync_brands(client, vehicle_type, period, progress)
        transaction.set_rollback(True)
    report("--dry-run: nada foi gravado.")
    return result


def _sync_brands(client, vehicle_type, period, progress):
    report = progress or (lambda message: None)

    reference = resolve_reference_table(client, period)
    report(f"Tabela de referência {reference} (código FIPE {reference.fipe_code})")

    created = updated = 0
    for brand_data in parsers.parse_brands(client.brands(reference.fipe_code, vehicle_type)):
        # update_or_create so a brand renamed by FIPE is corrected here.
        _, was_created = Brand.objects.update_or_create(
            vehicle_type=vehicle_type,
            fipe_code=brand_data.fipe_code,
            defaults={"name": brand_data.name},
        )
        if was_created:
            created += 1
            report(f"  + {brand_data.name}")
        else:
            updated += 1
    return created, updated


def sync(
    client,
    vehicle_type=VehicleType.CAR,
    period=None,
    brand_codes=None,
    limit=None,
    resume=False,
    dry_run=False,
    progress=None,
    progress_state=None,
):
    """Collect one reference table into the database and return the CrawlRun.

    A real run commits as it goes — that is what makes ``resume`` work. A dry
    run does the exact same work inside a transaction that is rolled back.
    """
    args = (client, vehicle_type, period, brand_codes, limit, resume, progress, progress_state)
    if not dry_run:
        return _sync(*args)

    report = progress or (lambda message: None)
    with transaction.atomic():
        run = _sync(*args)
        transaction.set_rollback(True)
    report("--dry-run: nada foi gravado.")
    return run


def _sync(client, vehicle_type, period, brand_codes, limit, resume, progress, progress_state):
    report = progress or (lambda message: None)
    state = progress_state if progress_state is not None else CrawlProgress()

    reference = resolve_reference_table(client, period)
    run = start_run(reference, vehicle_type, resume=resume)
    report(f"Tabela de referência {reference} (código FIPE {reference.fipe_code})")

    # A filtered or capped sweep never covers the whole table, so it must not
    # be marked COMPLETED — otherwise --resume would consider it finished.
    partial = bool(brand_codes) or limit is not None
    try:
        _walk_brands(
            client, run, reference, vehicle_type, brand_codes, limit, resume, report, state
        )
    except LimitReached:
        partial = True
        report(f"Limite de {limit} cotações atingido; execução parcial.")
    except Exception as exc:
        run.status = CrawlStatus.FAILED
        run.last_error = f"{type(exc).__name__}: {exc}"
        run.finished_at = timezone.now()
        run.save()
        raise

    if not partial:
        run.status = CrawlStatus.COMPLETED
        run.finished_at = timezone.now()
    run.save()
    return run


def _walk_brands(client, run, reference, vehicle_type, brand_codes, limit, resume, report, state):
    brands = parsers.parse_brands(client.brands(reference.fipe_code, vehicle_type))
    if brand_codes:
        wanted = {int(code) for code in brand_codes}
        brands = [b for b in brands if b.fipe_code in wanted]

    state.brands_total = len(brands)
    state.brands_done = 0
    state.quotes = run.quotes_created + run.quotes_updated

    for brand_data in brands:
        brand, _ = Brand.objects.get_or_create(
            vehicle_type=vehicle_type,
            fipe_code=brand_data.fipe_code,
            defaults={"name": brand_data.name},
        )
        checkpoint, _ = CrawlCheckpoint.objects.get_or_create(crawl_run=run, brand=brand)
        if resume and checkpoint.done:
            report(f"  {brand.name}: já concluída, pulando.")
            state.brands_done += 1
            continue

        try:
            _walk_models(client, run, reference, vehicle_type, brand, limit, report, state)
        finally:
            # Counters are kept in memory and flushed at brand boundaries: one
            # UPDATE per brand instead of one per quote.
            run.save()

        checkpoint.done = True
        checkpoint.save(update_fields=["done"])
        run.brands_done += 1
        state.brands_done += 1
        report(
            f"  {brand.name}: {state.models_total} modelos concluídos "
            f"({state.quotes} cotações acumuladas)"
        )


def _walk_models(client, run, reference, vehicle_type, brand, limit, report, state):
    models = parsers.parse_models(
        client.models(reference.fipe_code, vehicle_type, brand.fipe_code)
    )
    state.start_brand(brand.name, len(models))
    report(f"  {brand.name}: {len(models)} modelos a processar")

    for model_data in models:
        vehicle_model, _ = VehicleModel.objects.get_or_create(
            brand=brand,
            fipe_code=model_data.fipe_code,
            defaults={"name": model_data.name},
        )
        years = parsers.parse_model_years(
            client.model_years(
                reference.fipe_code, vehicle_type, brand.fipe_code, vehicle_model.fipe_code
            )
        )

        for year_data in years:
            model_year, _ = ModelYear.objects.get_or_create(
                vehicle_model=vehicle_model,
                fipe_year_code=year_data.fipe_year_code,
                defaults={"year": year_data.year, "fuel_type": year_data.fuel_type},
            )
            _upsert_quote(
                client, run, reference, vehicle_type, brand, vehicle_model, model_year
            )
            state.quotes = run.quotes_created + run.quotes_updated

            if limit is not None and state.quotes >= limit:
                raise LimitReached

        run.models_done += 1
        state.models_done += 1


def _upsert_quote(client, run, reference, vehicle_type, brand, vehicle_model, model_year):
    try:
        payload = client.price(
            reference.fipe_code,
            vehicle_type,
            brand.fipe_code,
            vehicle_model.fipe_code,
            model_year.fipe_year_code,
        )
    except FipeNotFound:
        # FIPE lists year/fuel combinations it cannot price. Skipping is correct.
        logger.info("sem cotação para %s", model_year.fipe_year_code)
        return

    quote_data = parsers.parse_quote(payload)
    _, created = PriceQuote.objects.update_or_create(
        model_year=model_year,
        reference_table=reference,
        defaults={
            "value": quote_data.value,
            "fipe_code": quote_data.fipe_code,
            "fuel_type": quote_data.fuel_type,
        },
    )
    if created:
        run.quotes_created += 1
    else:
        run.quotes_updated += 1
