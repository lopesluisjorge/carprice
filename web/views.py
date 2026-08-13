"""Screens over the collected FIPE data. Nothing here talks to the FIPE API."""

from django.shortcuts import render
from django.utils.http import urlencode

from crawler.models import FuelType
from crawler.services import scheduling

from web import codes
from web import queries
from web import search
from web.filters import SearchFilters

MAX_COMPARED = 4

YEAR_OPS = [("gte", "a partir de"), ("eq", "exatamente"), ("lte", "até")]
FUEL_LABELS = dict(FuelType.choices)


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
        "year_ops": YEAR_OPS,
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
    context = _search_context(filters)
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
    if vehicle_model is None:
        return render(
            request,
            "web/home.html",
            _search_context(SearchFilters()) | {"message": "Modelo não encontrado."},
            status=404,
        )
    return render(
        request,
        "web/model.html",
        {
            "vehicle_model": vehicle_model,
            "versions": queries.model_versions(vehicle_model),
            "reference_table": queries.latest_reference_table(),
            "back_to_search": request.GET.get("from", ""),
        },
    )


def detail(request):
    code = request.GET.get("v", "")
    model_year = codes.get(code)
    if model_year is None:
        return render(
            request,
            "web/home.html",
            _search_context(SearchFilters()) | {"message": "Veículo não encontrado."},
            status=404,
        )

    summary = queries.summarize(model_year)
    context = {
        "reference_table": queries.latest_reference_table(),
        "code": code,
        "summary": summary,
        "chart_series": [queries.chart_series(summary)],
        "has_history": len(summary["quotes"]) > 1,
    }
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

    context = {
        "reference_table": queries.latest_reference_table(),
        "summaries": summaries,
        "selection": ",".join(kept),
        "selection_query": urlencode({"v": ",".join(kept)}) if kept else "",
        "is_full": len(kept) >= MAX_COMPARED,
        "max_compared": MAX_COMPARED,
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
