"""Read-only queries over the crawler's tables.

Everything the web app knows about vehicles goes through here — the views never
touch the FIPE API, only what the crawler has already stored.
"""

from django.core.paginator import Paginator
from django.db.models import Count
from django.db.models import Max
from django.db.models import Min
from django.db.models import Prefetch

from crawler.models import ModelYear
from crawler.models import PriceQuote
from crawler.models import ReferenceTable
from crawler.models import VehicleModel
from crawler.models import ZERO_KM_YEAR

from web import codes
from web import search

# Windows offered on the detail and comparison screens, in months.
VARIATION_WINDOWS = [3, 6, 12]

PER_PAGE = 24


def period(reference_table):
    """A reference table as a single comparable number of months."""
    return reference_table.year * 12 + reference_table.month


def latest_reference_table():
    return ReferenceTable.objects.first()


# ModelYear.Meta.ordering is ["-year"], and Django drags an ordering field into
# the SELECT of a DISTINCT query — which would make it distinct over the pair and
# repeat every fuel once per year it appears in. Hence the empty order_by().
def available_fuels():
    """The fuels present in the data, not a fixed list — an unnamed FIPE code
    shows up as a bare number instead of disappearing from the filter."""
    return sorted(
        ModelYear.objects.values_list("fuel_type", flat=True).order_by().distinct()
    )


def available_years():
    years = (
        ModelYear.objects.exclude(year=ZERO_KM_YEAR)
        .values_list("year", flat=True)
        .order_by()
        .distinct()
    )
    return sorted(years, reverse=True)


def search_models(filters):
    """One page of cards, one card per VehicleModel."""
    reference = latest_reference_table()
    if reference is None:
        return Paginator([], PER_PAGE).get_page(1)

    model_years_qs = ModelYear.objects.all()
    if filters.fuels:
        model_years_qs = model_years_qs.filter(fuel_type__in=filters.fuels)
    if filters.year is not None:
        model_years_qs = model_years_qs.filter(**{filters.year_lookup: filters.year})

    ranked_ids = search.search(filters.term)
    if ranked_ids is not None:
        model_years_qs = model_years_qs.filter(vehicle_model_id__in=ranked_ids)

    rows = (
        PriceQuote.objects.filter(reference_table=reference, model_year__in=model_years_qs)
        .values("model_year__vehicle_model")
        .annotate(
            min_value=Min("value"),
            max_value=Max("value"),
            versions=Count("model_year", distinct=True),
        )
        # PriceQuote.Meta.ordering would otherwise join the GROUP BY and split
        # each model into one row per reference month.
        .order_by()
    )

    if ranked_ids is None:
        rows = rows.order_by(
            "model_year__vehicle_model__brand__name", "model_year__vehicle_model__name"
        )
        page = Paginator(rows, PER_PAGE).get_page(filters.page)
    else:
        position = {pk: index for index, pk in enumerate(ranked_ids)}
        ordered = sorted(rows, key=lambda row: position[row["model_year__vehicle_model"]])
        page = Paginator(ordered, PER_PAGE).get_page(filters.page)

    # Only the page's models are loaded, never the whole result.
    ids = [row["model_year__vehicle_model"] for row in page]
    models = VehicleModel.objects.select_related("brand").in_bulk(ids)
    page.object_list = [
        row
        | {
            "vehicle_model": models[row["model_year__vehicle_model"]],
            "code": codes.encode_model(models[row["model_year__vehicle_model"]]),
            "reference_table": reference,
        }
        for row in page
    ]
    return page


def model_versions(vehicle_model):
    """Every version of a model, newest first — the model page ignores the
    search filters, so its URL always shows the same thing."""
    reference = latest_reference_table()
    return (
        ModelYear.objects.filter(vehicle_model=vehicle_model)
        .select_related("vehicle_model__brand")
        .prefetch_related(
            Prefetch(
                "quotes",
                queryset=PriceQuote.objects.filter(reference_table=reference),
                to_attr="current_quotes",
            )
        )
        .order_by("-year", "fuel_type")
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
