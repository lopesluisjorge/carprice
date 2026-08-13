"""Read-only queries over the crawler's tables.

Everything the web app knows about vehicles goes through here — the views never
touch the FIPE API, only what the crawler has already stored.
"""

from crawler.models import Brand
from crawler.models import ModelYear
from crawler.models import PriceQuote
from crawler.models import VehicleModel
from crawler.models import VehicleType

# Windows offered on the detail and comparison screens, in months.
VARIATION_WINDOWS = [3, 6, 12]


def period(reference_table):
    """A reference table as a single comparable number of months."""
    return reference_table.year * 12 + reference_table.month


def brands(vehicle_type=VehicleType.CAR):
    """Only brands with collected prices — the rest would open an empty select."""
    return (
        Brand.objects.filter(
            vehicle_type=vehicle_type,
            models__model_years__quotes__isnull=False,
        )
        .distinct()
        .order_by("name")
    )


def vehicle_models(brand_id):
    return (
        VehicleModel.objects.filter(brand_id=brand_id, model_years__quotes__isnull=False)
        .distinct()
        .order_by("name")
    )


def model_years(vehicle_model_id):
    return (
        ModelYear.objects.filter(vehicle_model_id=vehicle_model_id, quotes__isnull=False)
        .select_related("vehicle_model__brand")
        .distinct()
        .order_by("-year", "fuel_type")
    )


def history(model_year):
    """Every collected quote for a version, oldest first."""
    return list(
        PriceQuote.objects.filter(model_year=model_year)
        .select_related("reference_table")
        .order_by("reference_table__year", "reference_table__month")
    )


def variation(quotes, months):
    """Change between the newest quote and the one ``months`` back.

    FIPE months are only present if they were collected, so an exact hit is not
    guaranteed: the closest month at or before the target is used instead, and
    returned along with the numbers so the screen can say which month it
    actually compared against.
    """
    if not quotes:
        return None
    latest = quotes[-1]
    target = period(latest.reference_table) - months
    older = [q for q in quotes[:-1] if period(q.reference_table) <= target]
    if not older:
        return None
    previous = older[-1]
    delta = latest.value - previous.value
    return {
        "months": months,
        "reference_table": previous.reference_table,
        "previous_value": previous.value,
        "delta": delta,
        "percent": float(delta / previous.value * 100) if previous.value else None,
    }


def variations(quotes):
    """One entry per window; a window with no older quote keeps only its label."""
    return [variation(quotes, months) or {"months": months} for months in VARIATION_WINDOWS]


def summarize(model_year):
    """Everything a screen shows about one version."""
    quotes = history(model_year)
    return {
        "model_year": model_year,
        "quotes": quotes,
        "latest": quotes[-1] if quotes else None,
        "variations": variations(quotes),
    }


def chart_series(summary):
    """One ApexCharts series: month label plus value, oldest first."""
    return {
        "name": str(summary["model_year"].vehicle_model),
        "data": [
            {
                "x": f"{quote.reference_table.month:02d}/{quote.reference_table.year}",
                "y": float(quote.value),
            }
            for quote in summary["quotes"]
        ],
    }
