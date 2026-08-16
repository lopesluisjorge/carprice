"""The on-demand collection worker's engine.

Runs one CollectionRequest at a time, spending a bounded number of FIPE
requests. Everything here assumes a single process: the quota is a sliding
window inside one FipeClient, and a second worker would silently double it.
"""

import contextlib
import fcntl
import logging
from datetime import timedelta

from django.utils import timezone

from crawler.models import CollectionRequest
from crawler.models import CollectionStatus
from crawler.models import PriceQuote
from crawler.models import QuoteLookup
from crawler.models import QuoteLookupStatus
from crawler.services import scheduling
from crawler.services import sync

logger = logging.getLogger(__name__)

DEFAULT_BUDGET = 1500

# How long a refusal holds in the month FIPE is still editing. Long enough that
# a model searched every day does not re-ask daily, short enough that a version
# priced mid-month appears within the week.
NOT_FOUND_RECHECK = timedelta(days=7)


class QueueBusy(Exception):
    """Another worker holds the queue lock."""


@contextlib.contextmanager
def queue_lock(path):
    """Hold an exclusive, non-blocking lock on `path`.

    A file lock rather than a database row because the OS releases it when the
    process dies, which is exactly the failure this must survive. It guards a
    single machine, not a cluster — the quota lives in one process's memory
    anyway.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise QueueBusy(f"outro worker já detém {path}") from exc
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def pending_requests():
    """Requests with work left, oldest first."""
    return (
        CollectionRequest.objects.filter(items__status=CollectionStatus.PENDING)
        .distinct()
        .order_by("created_at")
    )


def reclaim_stale_requests():
    """Return to PARTIAL anything a dead worker left RUNNING.

    Without this the request would keep a status nobody ever moves again, and
    its pending items would never be picked up.
    """
    return CollectionRequest.objects.filter(status=CollectionStatus.RUNNING).update(
        status=CollectionStatus.PARTIAL
    )


def _work_plan(vehicle_model, today):
    """``(recency, period, version)`` for one model, newest period first.

    Sorted by how recent the period is *across versions*, not version by
    version: with the budget exhausted, a complete snapshot of the current month
    is worth more than the full history of two versions and nothing of the rest.
    """
    plan = []
    for version in vehicle_model.model_years.all():
        for recency, period in enumerate(scheduling.periods_for(version.year, today)):
            plan.append((recency, period, version))
    plan.sort(key=lambda row: (row[0], row[2].pk))
    return plan


def _newest_reference(references):
    """The table FIPE is still editing — the only one a refusal can outlive."""
    newest_period = max(references, default=None)
    return references.get(newest_period)


def _settled(version, reference, newest):
    """Is this pair done, i.e. would asking FIPE again teach nothing?

    Two ways to be done. The quote is stored — or FIPE refused to price it,
    which QuoteLookup is what remembers. The refusal is final in a closed month,
    whose table never changes again; in the newest one it only holds for
    NOT_FOUND_RECHECK, because FIPE does add prices during the month.

    Without this the pairs FIPE refuses were re-asked on every pass forever, and
    a model whose refusals alone exhausted the budget never left PENDING: the
    next pass spent the same budget on the same 404s.
    """
    if PriceQuote.objects.filter(model_year=version, reference_table=reference).exists():
        return True

    checked_at = (
        QuoteLookup.objects.filter(
            model_year=version,
            reference_table=reference,
            status=QuoteLookupStatus.NOT_FOUND,
        )
        .values_list("checked_at", flat=True)
        .first()
    )
    if checked_at is None:
        return False
    return reference != newest or timezone.now() - checked_at < NOT_FOUND_RECHECK


def _collect_model(client, request, item, references, budget, today):
    """Spend at most `budget` requests on one model. Returns what it spent."""
    vehicle_model = item.vehicle_model
    brand = vehicle_model.brand
    newest = _newest_reference(references)
    spent = 0

    for _, period, version in _work_plan(vehicle_model, today):
        if spent >= budget:
            return spent
        reference = references.get(period)
        if reference is None:
            continue  # FIPE has no table for that month; not an error.
        if _settled(version, reference, newest):
            continue

        outcome = sync.upsert_quote(
            client, reference, request.vehicle_type, brand, vehicle_model, version
        )
        spent += 1
        if outcome is sync.QuoteOutcome.CREATED:
            request.quotes_created += 1
        elif outcome is sync.QuoteOutcome.UPDATED:
            request.quotes_updated += 1
        else:
            request.quotes_missing += 1
    return spent


def _model_has_work_left(item, references, today):
    """Is there any pair left for this model that asking FIPE would settle?"""
    newest = _newest_reference(references)
    for _, period, version in _work_plan(item.vehicle_model, today):
        reference = references.get(period)
        if reference is None:
            continue
        if not _settled(version, reference, newest):
            return True
    return False


def process_request(client, request, budget=DEFAULT_BUDGET, today=None, progress=None):
    """Work one request until it finishes or the budget runs out.

    Leaves the request COMPLETED or PARTIAL. An item interrupted mid-way stays
    PENDING and is redone on the next pass — cheaply, because pairs already
    stored are skipped.
    """
    report = progress or (lambda message: None)
    today = today or timezone.localdate()

    request.status = CollectionStatus.RUNNING
    if request.started_at is None:
        request.started_at = timezone.now()
    request.save(update_fields=["status", "started_at"])

    references = sync.reference_table_map(client)
    spent = 0

    for item in request.items.filter(status=CollectionStatus.PENDING):
        if spent >= budget:
            break
        spent += _collect_model(
            client, request, item, references, budget - spent, today
        )
        if spent >= budget and _model_has_work_left(item, references, today):
            break  # item stays PENDING; the next pass resumes it
        item.status = CollectionStatus.COMPLETED
        item.finished_at = timezone.now()
        item.save(update_fields=["status", "finished_at"])
        request.models_done += 1
        report(f"  {item.vehicle_model}: concluído ({spent} requisições na passada)")

    request.requests_spent += spent
    still_pending = request.items.filter(status=CollectionStatus.PENDING).exists()
    request.status = (
        CollectionStatus.PARTIAL if still_pending else CollectionStatus.COMPLETED
    )
    if not still_pending:
        request.finished_at = timezone.now()
    request.save()
    return spent
