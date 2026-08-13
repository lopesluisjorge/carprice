"""Scheduling for on-demand collection.

Never touches the FIPE API: it turns a search into rows the worker will later
execute. That is what keeps the web app on the right side of the boundary — it
writes a request, it never runs a collection.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from crawler.models import ZERO_KM_YEAR
from crawler.models import CollectionItem
from crawler.models import CollectionRequest
from crawler.models import VehicleType

# Recent resolution: current month, then three, six and twelve months back.
# Matches the 3/6/12 variation windows the detail screen already shows.
MONTHS_BACK = (0, 3, 6, 12)

# A model asked for inside this window is not asked for again.
COVERAGE_WINDOW = timedelta(hours=48)


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


def request_collection(term, model_ids, vehicle_type=VehicleType.CAR):
    """Schedule the collection of `model_ids`, skipping what is already covered.

    Returns the request that now covers the search — a fresh one, the existing
    one when everything was already asked for, or None when there is nothing to
    collect. Never calls FIPE.
    """
    if not term or not model_ids:
        return None

    covered = covered_model_ids()
    missing = [model_id for model_id in model_ids if model_id not in covered]
    if not missing:
        return covering_request(model_ids)

    with transaction.atomic():
        request = CollectionRequest.objects.create(term=term, vehicle_type=vehicle_type)
        CollectionItem.objects.bulk_create(
            [
                CollectionItem(request=request, vehicle_model_id=model_id, rank=rank)
                # rank comes from the caller's order, which is the FTS ranking.
                for rank, model_id in enumerate(missing)
            ]
        )
    return request
