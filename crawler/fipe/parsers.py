"""Turn raw FIPE JSON into plain dataclasses.

No Django imports here on purpose: parsing must be testable without a database
and must not depend on the models it eventually feeds.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

MONTHS = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

FUEL_BY_LABEL = {
    "gasolina": 1,
    "alcool": 2,
    "etanol": 2,
    "diesel": 3,
    "eletrico": 4,
    "flex": 5,
}

ACCENTS = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüç", "aaaaaeeeeiiiiooooouuuuc")


class ParseError(ValueError):
    """The payload did not look like what the FIPE API is supposed to return."""


def _normalize(text):
    return str(text).strip().lower().translate(ACCENTS)


@dataclass(frozen=True)
class ReferenceTableData:
    fipe_code: int
    month: int
    year: int


@dataclass(frozen=True)
class BrandData:
    fipe_code: int
    name: str


@dataclass(frozen=True)
class VehicleModelData:
    fipe_code: int
    name: str


@dataclass(frozen=True)
class ModelYearData:
    fipe_year_code: str
    year: int
    fuel_type: int


@dataclass(frozen=True)
class QuoteData:
    value: Decimal
    fipe_code: str
    brand_name: str
    model_name: str
    year: int
    fuel_type: int


def parse_month_year(label):
    """``"junho/2025 "`` or ``"junho de 2025"`` -> ``(6, 2025)``."""
    normalized = _normalize(label)
    match = re.match(r"^([a-z]+)\s*(?:/|de)\s*(\d{4})$", normalized)
    if not match:
        raise ParseError(f"período não reconhecido: {label!r}")
    month_name, year = match.groups()
    if month_name not in MONTHS:
        raise ParseError(f"mês não reconhecido: {label!r}")
    return MONTHS[month_name], int(year)


def parse_money(raw):
    """``"R$ 22.000,00"`` -> ``Decimal("22000.00")``."""
    digits = re.sub(r"[^\d,.-]", "", str(raw)).replace(".", "").replace(",", ".")
    try:
        return Decimal(digits)
    except InvalidOperation as exc:
        raise ParseError(f"valor não reconhecido: {raw!r}") from exc


def parse_fuel_type(label, default=1):
    """``"1992 Gasolina"`` or ``"Álcool"`` -> the FIPE fuel code."""
    normalized = _normalize(label)
    for name, code in FUEL_BY_LABEL.items():
        if name in normalized:
            return code
    return default


def parse_reference_tables(payload):
    return [
        ReferenceTableData(fipe_code=int(item["Codigo"]), month=month, year=year)
        for item in payload
        for month, year in [parse_month_year(item["Mes"])]
    ]


def parse_brands(payload):
    return [
        BrandData(fipe_code=int(item["Value"]), name=str(item["Label"]).strip())
        for item in payload
    ]


def parse_models(payload):
    return [
        VehicleModelData(fipe_code=int(item["Value"]), name=str(item["Label"]).strip())
        for item in payload
    ]


def parse_model_years(payload):
    """``[{"Label": "1992 Gasolina", "Value": "1992-1"}]`` -> dataclasses.

    The year comes from ``Value`` (``"<year>-<fuel>"``) because ``Label`` uses
    ``"32000"`` for zero km vehicles inconsistently across endpoints.
    """
    years = []
    for item in payload:
        code = str(item["Value"]).strip()
        year_part, _, fuel_part = code.partition("-")
        try:
            year = int(year_part)
        except ValueError as exc:
            raise ParseError(f"código de ano inválido: {code!r}") from exc
        fuel_type = int(fuel_part) if fuel_part.isdigit() else parse_fuel_type(item.get("Label", ""))
        years.append(ModelYearData(fipe_year_code=code, year=year, fuel_type=fuel_type))
    return years


def parse_quote(payload):
    try:
        return QuoteData(
            value=parse_money(payload["Valor"]),
            fipe_code=str(payload["CodigoFipe"]).strip(),
            brand_name=str(payload["Marca"]).strip(),
            model_name=str(payload["Modelo"]).strip(),
            year=int(payload["AnoModelo"]),
            fuel_type=parse_fuel_type(payload.get("Combustivel", "")),
        )
    except KeyError as exc:
        raise ParseError(f"cotação sem o campo {exc}") from exc
