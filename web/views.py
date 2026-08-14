"""Screens over the collected FIPE data. Nothing here talks to the FIPE API."""

from django.shortcuts import render
from django.urls import reverse

from crawler.models import FuelType
from crawler.services import scheduling

from web import codes
from web import queries
from web import search
from web import selection
from web.filters import PRICE_STEPS
from web.filters import SearchFilters

MAX_COMPARED = selection.MAX_COMPARED

YEAR_OPS = [("gte", "a partir de"), ("eq", "exatamente"), ("lte", "até")]
# No "exatamente" here: with fixed steps it would match only the exact amount.
PRICE_OPS = [("gte", "a partir de"), ("lte", "até")]
SORT_OPTIONS = [
    ("", "relevância"),
    ("price_asc", "menor preço"),
    ("price_desc", "maior preço"),
]
FUEL_LABELS = dict(FuelType.choices)


def _price_label(value):
    """R$ 50 mil — the full "R$ 50.000,00" only clutters a narrow select. A value
    that came from a hand-written URL keeps its digits, so it is never rounded
    into a lie."""
    if value % 1000 == 0:
        return f"R$ {value // 1000} mil"
    return f"R$ {value:,}".replace(",", ".")


def _price_steps(current):
    """The fixed steps, plus whatever arrived in the URL if it is not one of
    them: a shared link has to come back showing what it was sharing."""
    steps = list(PRICE_STEPS)
    if current is not None and current not in steps:
        steps.append(current)
    return [(value, _price_label(value)) for value in sorted(steps)]


def _search_context(filters):
    """Everything the search screen needs.

    Also used by the screens that fall back to the search when the code in the
    URL does not resolve.
    """
    page = queries.search_models(filters)
    return {
        "filters": filters,
        "page": page,
        "fuels": [
            (code, FUEL_LABELS.get(code, f"Combustível {code}"))
            for code in queries.available_fuels()
        ],
        "years": queries.available_years(),
        "brands": [
            (codes.encode_brand(brand), brand.name) for brand in queries.available_brands()
        ],
        "year_ops": YEAR_OPS,
        "price_ops": PRICE_OPS,
        "price_steps": _price_steps(filters.price),
        "sort_options": SORT_OPTIONS,
        "previous_url": (
            filters.querystring(page=page.previous_page_number()) if page.has_previous() else ""
        ),
        "next_url": (
            filters.querystring(page=page.next_page_number()) if page.has_next() else ""
        ),
        "reference_table": queries.latest_reference_table(),
    }


def home(request):
    filters = SearchFilters.from_query(request.GET)
    tray = selection.from_request(request)
    context = _search_context(filters)
    context |= selection.context(tray)
    # "limpar" keeps the search itself, only the tray goes.
    query = filters.querystring()
    context["selection_clear_url"] = (
        f"{reverse('web:home')}?{query}" if query else reverse("web:home")
    )
    # Only a term schedules work: tweaking the fuel or year filter must not
    # queue thousands of FIPE requests. The whole match is scheduled, not just
    # the visible page.
    context["collection"] = scheduling.request_collection(
        filters.term, search.search(filters.term) or []
    )
    if request.headers.get("HX-Request"):
        return render(request, "web/partials/results.html", context)
    return render(request, "web/home.html", context)


def model_detail(request):
    vehicle_model = codes.get_model(request.GET.get("m", ""))
    tray = selection.from_request(request)
    if vehicle_model is None:
        return render(
            request,
            "web/home.html",
            _search_context(SearchFilters())
            | selection.context(tray, reverse("web:home"))
            | {"message": "Modelo não encontrado."},
            status=404,
        )

    back_to_search = request.GET.get("from", "")
    params = {"m": codes.encode_model(vehicle_model), "from": back_to_search}
    versions = list(queries.model_versions(vehicle_model))
    for version in versions:
        # Toggling keeps the reader on this page: picking four versions of the
        # same model should not mean four round trips to the comparison screen.
        version.code = codes.encode(version)
        version.in_tray = version.code in tray
        after_toggle = selection.toggled(tray, version.code)
        version.toggle_url = (
            None
            if after_toggle is None
            else selection.url(reverse("web:model"), after_toggle, params)
        )

    return render(
        request,
        "web/model.html",
        {
            "vehicle_model": vehicle_model,
            "versions": versions,
            "reference_table": queries.latest_reference_table(),
            "back_to_search": back_to_search,
        }
        | selection.context(tray, reverse("web:model"), params),
    )


def detail(request):
    code = request.GET.get("v", "")
    model_year = codes.get(code)
    tray = selection.from_request(request)
    if model_year is None:
        return render(
            request,
            "web/home.html",
            _search_context(SearchFilters())
            | selection.context(tray, reverse("web:home"))
            | {"message": "Veículo não encontrado."},
            status=404,
        )

    summary = queries.summarize(model_year)
    after_toggle = selection.toggled(tray, code)
    context = {
        "reference_table": queries.latest_reference_table(),
        "code": code,
        "summary": summary,
        "chart_series": [queries.chart_series(summary)],
        "has_history": len(summary["quotes"]) > 1,
        "in_tray": code in tray,
        "toggle_url": (
            None
            if after_toggle is None
            else selection.url(reverse("web:detail"), after_toggle, {"v": code})
        ),
    }
    context |= selection.context(tray, reverse("web:detail"), {"v": code})
    return render(request, "web/detail.html", context)


def compare(request):
    """Up to four versions side by side, with the selection in the querystring."""
    selected = codes.parse_list(request.GET.get("v"), MAX_COMPARED)
    added = request.GET.get("add", "").strip()
    rejected = bool(added) and added not in selected and len(selected) >= MAX_COMPARED
    if added and added not in selected and not rejected:
        selected.append(added)

    summaries = []
    for code in selected:
        model_year = codes.get(code)
        if model_year is not None:
            summaries.append(queries.summarize(model_year) | {"code": code})

    kept = [summary["code"] for summary in summaries]
    for summary in summaries:
        # What the querystring becomes when this card's "remover" is clicked.
        summary["without_me"] = ",".join(code for code in kept if code != summary["code"])

    context = selection.context(kept) | {
        "reference_table": queries.latest_reference_table(),
        "summaries": summaries,
        # Transposed here because the table reads one window per row, across
        # vehicles — and a template cannot index a list by a loop variable.
        "variation_rows": [
            {"months": months, "cells": [summary["variations"][index] for summary in summaries]}
            for index, months in enumerate(queries.VARIATION_WINDOWS)
        ],
        "chart_series": [queries.chart_series(summary) for summary in summaries],
        "has_history": any(len(summary["quotes"]) > 1 for summary in summaries),
    }
    if rejected:
        context["message"] = f"O comparador aceita no máximo {MAX_COMPARED} versões."
    return render(request, "web/compare.html", context)
