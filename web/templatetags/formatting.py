"""Brazilian number formatting, done here so no locale has to be installed."""

from django import template

from crawler.models import ZERO_KM_YEAR

from web import codes

register = template.Library()

EMPTY = "—"


def _decimal_br(value, places=2):
    text = f"{abs(value):,.{places}f}"
    # "1,234.56" -> "1.234,56", swapping both separators in one pass.
    return text.translate(str.maketrans({",": ".", ".": ","}))


@register.filter
def brl(value):
    if value is None:
        return EMPTY
    sign = "-" if value < 0 else ""
    return f"{sign}R$ {_decimal_br(value)}"


@register.filter
def signed_brl(value):
    if value is None:
        return EMPTY
    sign = "+" if value > 0 else "-" if value < 0 else ""
    return f"{sign}R$ {_decimal_br(value)}"


@register.filter
def percent(value):
    if value is None:
        return EMPTY
    sign = "+" if value > 0 else "-" if value < 0 else ""
    return f"{sign}{_decimal_br(value, 1)}%"


@register.filter
def model_year_label(model_year):
    """The year as shown to the reader — FIPE's 32000 means brand new."""
    return "0 km" if model_year.year == ZERO_KM_YEAR else str(model_year.year)


@register.filter
def code(model_year):
    """The shareable code a version travels under in the querystring."""
    return codes.encode(model_year)
