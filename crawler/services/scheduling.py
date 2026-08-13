"""Scheduling for on-demand collection.

Never touches the FIPE API: it turns a search into rows the worker will later
execute. That is what keeps the web app on the right side of the boundary — it
writes a request, it never runs a collection.
"""

from crawler.models import ZERO_KM_YEAR

# Recent resolution: current month, then three, six and twelve months back.
# Matches the 3/6/12 variation windows the detail screen already shows.
MONTHS_BACK = (0, 3, 6, 12)


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
