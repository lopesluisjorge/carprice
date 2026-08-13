"""Shareable identifiers for a model/year version.

A primary key would work in a URL, but only against this database — and the
comparison link is meant to survive being pasted somewhere else. So the code is
built out of the FIPE codes instead:

    ``1-21-4712-2017-5`` -> car, brand 21, model 4712, year 2017, flex

The leading vehicle type keeps the code unambiguous once motorcycles and trucks
are collected: FIPE numbers its brands per type, so brand 21 means one thing for
cars and another for motorcycles.
"""

from crawler.models import ModelYear
from crawler.models import VehicleModel

SEPARATOR = "-"
PARTS = 5
MODEL_PARTS = 3


def encode(model_year):
    brand = model_year.vehicle_model.brand
    return SEPARATOR.join(
        [
            str(brand.vehicle_type),
            str(brand.fipe_code),
            str(model_year.vehicle_model.fipe_code),
            model_year.fipe_year_code,
        ]
    )


def decode(code):
    """Turn a code into lookup filters, or None if it is not a valid code."""
    parts = code.strip().split(SEPARATOR)
    if len(parts) != PARTS or not all(part.isdigit() for part in parts):
        return None
    vehicle_type, brand, model, year, fuel = parts
    return {
        "vehicle_model__brand__vehicle_type": int(vehicle_type),
        "vehicle_model__brand__fipe_code": int(brand),
        "vehicle_model__fipe_code": int(model),
        "fipe_year_code": f"{year}{SEPARATOR}{fuel}",
    }


def get(code):
    """The ModelYear a code points to, or None."""
    filters = decode(code)
    if filters is None:
        return None
    return ModelYear.objects.select_related("vehicle_model__brand").filter(**filters).first()


def encode_model(vehicle_model):
    brand = vehicle_model.brand
    return SEPARATOR.join(
        [str(brand.vehicle_type), str(brand.fipe_code), str(vehicle_model.fipe_code)]
    )


def decode_model(code):
    """Same discipline as the version code, three parts instead of five."""
    parts = code.strip().split(SEPARATOR)
    if len(parts) != MODEL_PARTS or not all(part.isdigit() for part in parts):
        return None
    vehicle_type, brand, model = parts
    return {
        "brand__vehicle_type": int(vehicle_type),
        "brand__fipe_code": int(brand),
        "fipe_code": int(model),
    }


def get_model(code):
    """The VehicleModel a code points to, or None."""
    filters = decode_model(code)
    if filters is None:
        return None
    return VehicleModel.objects.select_related("brand").filter(**filters).first()


def parse_list(raw, limit):
    """Split a ``?v=`` value into at most ``limit`` codes, without repeats."""
    codes = []
    for code in (raw or "").split(","):
        code = code.strip()
        if code and code not in codes:
            codes.append(code)
    return codes[:limit]
