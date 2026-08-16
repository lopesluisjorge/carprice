"""Scheduling for on-demand collection.

Never touches the FIPE API: it turns a search into rows the worker will later
execute. That is what keeps the web app on the right side of the boundary — it
writes a request, it never runs a collection.

Everything below the constants exists because the trigger is an anonymous GET.
A search is not a cheap write: one scheduled model costs its versions times its
periods in FIPE requests, and the quota is 40 a minute. So the caller is not
trusted with how much work it may enqueue — the three limits here are, in
order, how short a term may be, how many models one search may schedule, and
how deep the queue may get before scheduling stops entirely.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from crawler.models import ZERO_KM_YEAR
from crawler.models import CollectionItem
from crawler.models import CollectionRequest
from crawler.models import CollectionStatus
from crawler.models import VehicleType

# Recent resolution: current month, then three, six and twelve months back.
# Matches the 3/6/12 variation windows the detail screen already shows.
MONTHS_BACK = (0, 3, 6, 12)

# A model asked for inside this window is not asked for again.
COVERAGE_WINDOW = timedelta(hours=48)

# Shorter than this schedules nothing. Search still works and still shows its
# cards — only the collection is withheld. A single letter is a prefix match:
# `?q=a` matched 450 models on a database holding just five brands, which is
# some 25 thousand FIPE requests and ten hours of worker, from one page load.
#
# Two, not three, because "up" is a car. MAX_MODELS below is what actually
# bounds a broad search, so this only has to reject the degenerate case of one
# letter — and a floor of three would quietly make the VW up! the one model
# nobody can ever schedule.
MIN_TERM_LENGTH = 2

# How many models one search may schedule, best-ranked first. Roughly the
# screenful the person is actually looking at (web's page is 24), not the whole
# match — the tail of a broad search is the part nobody scrolls to.
#
# The models left out stay uncovered on purpose, so searching the same term
# again after the queue drains schedules the next slice. Coverage grows in
# pages instead of in one burst, and neither a curious visitor nor a hostile
# one can turn a single request into days of collection.
MAX_MODELS = 24

# Backpressure for the whole queue, which is what the per-search cap alone
# cannot give: distinct terms would otherwise add up to the entire catalogue.
# Past this many models still waiting, a search schedules nothing and says so
# by returning whatever already covers it. At ~55 requests a model this is
# about eleven hours of backlog — a queue deeper than that is not serving
# anybody anyway.
MAX_PENDING_MODELS = 500

# Read off the field instead of repeated here, so the two cannot drift apart.
# Postgres enforces varchar(200) and raises DataError; SQLite silently stores
# the overflow. Truncating at the write is what keeps a long ?q= from being a
# 500 on one engine and a lie on the other.
TERM_MAX_LENGTH = CollectionRequest._meta.get_field("term").max_length


def _shift(year, month, months):
    """``(2026, 1)`` shifted back 3 months -> ``(2025, 10)``."""
    total = year * 12 + (month - 1) - months
    return total // 12, total % 12 + 1


def periods_for(version_year, today):
    """The ``(year, month)`` periods to collect for one version, newest first.

    The yearly ladder stops one year after the version's own year: a 2020
    version has no price in the 2005 table, and asking is a guaranteed 404 that
    still costs a slot of the quota. A 0 km vehicle (year 32000) is a
    current-year car, so it gets no ladder at all.
    """
    periods = {_shift(today.year, today.month, months) for months in MONTHS_BACK}
    if version_year != ZERO_KM_YEAR:
        for year in range(version_year + 1, today.year):
            periods.add((year, today.month))
    return sorted(periods, reverse=True)


def covered_model_ids(now=None):
    """Models already asked for by a request inside the coverage window.

    Every status counts, pending included: a slow queue must not produce
    duplicate requests for models nobody has collected yet.
    """
    cutoff = (now or timezone.now()) - COVERAGE_WINDOW
    return set(
        CollectionItem.objects.filter(request__created_at__gte=cutoff).values_list(
            "vehicle_model_id", flat=True
        )
    )


def covering_request(model_ids, now=None):
    """The most recent request inside the window touching any of these models."""
    cutoff = (now or timezone.now()) - COVERAGE_WINDOW
    return (
        CollectionRequest.objects.filter(
            created_at__gte=cutoff, items__vehicle_model_id__in=model_ids
        )
        .order_by("-created_at")
        .first()
    )


def pending_models():
    """Models still waiting in the queue, across every request."""
    return CollectionItem.objects.filter(status=CollectionStatus.PENDING).count()


def request_collection(term, model_ids, vehicle_type=VehicleType.CAR):
    """Schedule the collection of `model_ids`, skipping what is already covered.

    Returns the request that now covers the search — a fresh one, the existing
    one when everything was already asked for or when the queue is too deep to
    take more, or None when there is nothing to collect. Never calls FIPE.

    At most MAX_MODELS models are scheduled, best-ranked first; the caller
    passes the whole match and does not decide how much of it is collected.
    """
    term = (term or "").strip()
    if len(term) < MIN_TERM_LENGTH or not model_ids:
        return None

    covered = covered_model_ids()
    missing = [model_id for model_id in model_ids if model_id not in covered]
    if not missing:
        return covering_request(model_ids)

    # Checked after coverage so a repeated search still reports its request
    # instead of going quiet the moment the queue fills up.
    if pending_models() >= MAX_PENDING_MODELS:
        return covering_request(model_ids)

    missing = missing[:MAX_MODELS]

    with transaction.atomic():
        request = CollectionRequest.objects.create(
            term=term[:TERM_MAX_LENGTH], vehicle_type=vehicle_type
        )
        CollectionItem.objects.bulk_create(
            [
                CollectionItem(request=request, vehicle_model_id=model_id, rank=rank)
                # rank comes from the caller's order, which is the FTS ranking.
                for rank, model_id in enumerate(missing)
            ]
        )
    return request
