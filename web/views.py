"""Screens over the collected FIPE data. Nothing here talks to the FIPE API."""

from django.shortcuts import render

from crawler.models import ReferenceTable

from web import codes
from web import queries

MAX_COMPARED = 4

# The year select posts under a different name on each screen; the cascade
# fragments echo it back, so only these two are accepted.
FIELD_NAMES = {"v", "add"}


def _base_context():
    return {"brands": queries.brands(), "reference_table": ReferenceTable.objects.first()}


def _int(value):
    """Query params reach the selects as text and may be empty or hand-edited."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _field(request):
    name = request.GET.get("field", "v")
    return name if name in FIELD_NAMES else "v"


def home(request):
    return render(request, "web/home.html", _base_context())


def model_options(request):
    """HTMX fragment: the model select for a brand, plus an emptied year select."""
    return render(
        request,
        "web/partials/cascade_tail.html",
        {
            "vehicle_models": queries.vehicle_models(_int(request.GET.get("brand"))),
            "field": _field(request),
        },
    )


def year_options(request):
    """HTMX fragment: the year select for a model."""
    return render(
        request,
        "web/partials/year_select.html",
        {
            "model_years": queries.model_years(_int(request.GET.get("model"))),
            "field": _field(request),
        },
    )


def detail(request):
    code = request.GET.get("v", "")
    model_year = codes.get(code)
    context = _base_context()
    if model_year is None:
        context["message"] = "Veículo não encontrado. Refaça a busca."
        return render(request, "web/home.html", context, status=404)

    summary = queries.summarize(model_year)
    context |= {
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

    context = _base_context()
    context |= {
        "summaries": summaries,
        "selection": ",".join(kept),
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
