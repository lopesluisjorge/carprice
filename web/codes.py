"""Shareable identifiers for a brand, a model and a model/year version.

A primary key would work in a URL, but only against this database — and the
comparison link is meant to survive being pasted somewhere else. So the codes are
built out of the FIPE codes instead, and told apart by how many parts they have:

    ``1-21``               -> car, brand 21
    ``1-21-4712``          -> car, brand 21, model 4712
    ``1-21-4712-2017-5``   -> the same model, year 2017, flex

The leading vehicle type keeps them unambiguous once motorcycles and trucks are
collected: FIPE numbers its brands per type, so brand 21 means one thing for cars
and another for motorcycles.
"""

from crawler.models import Brand
from crawler.models import ModelYear
from crawler.models import VehicleModel

SEPARATOR = "-"
PARTS = 5
MODEL_PARTS = 3
BRAND_PARTS = 2

# `str.isdigit()` is not the guarantee `int()` needs, and both gaps were
# reachable from a querystring anybody can type:
#
#   ?m=1-²-4712        '²'.isdigit() is True and int('²') raises ValueError
#   ?m=1-21-<5000 9s>  CPython refuses to convert past 4300 digits
#
# Either one was an uncaught ValueError — a 500 on /veiculo/, /modelo/ and the
# brand filter. ASCII digits with a length cap is the whole fix: no FIPE code
# comes near ten of them, the largest being the 32000 that means 0 km.
MAX_PART_DIGITS = 10


def _parts(code, count):
    """The `count` numeric parts of `code`, or None if it is not one.

    Shared by the three decoders so a code can never be validated one way here
    and another way there — the only difference between them is how many parts
    they expect.
    """
    parts = code.strip().split(SEPARATOR)
    if len(parts) != count:
        return None
    if not all(
        part.isascii() and part.isdigit() and len(part) <= MAX_PART_DIGITS for part in parts
    ):
        return None
    return parts


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
    parts = _parts(code, PARTS)
    if parts is None:
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
    parts = _parts(code, MODEL_PARTS)
    if parts is None:
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
    return (
        VehicleModel.objects.select_related("brand", "engine_type").filter(**filters).first()
    )


def encode_brand(brand):
    return SEPARATOR.join([str(brand.vehicle_type), str(brand.fipe_code)])


def decode_brand(code):
    """Same discipline as the other codes, two parts this time.

    The vehicle type is not decoration: FIPE numbers its brands per type, so a
    bare 21 would mean one brand for cars and another for motorcycles the day
    motorcycles are collected.
    """
    parts = _parts(code, BRAND_PARTS)
    if parts is None:
        return None
    vehicle_type, fipe_code = parts
    return {"vehicle_type": int(vehicle_type), "fipe_code": int(fipe_code)}


def get_brand(code):
    """The Brand a code points to, or None."""
    filters = decode_brand(code)
    if filters is None:
        return None
    return Brand.objects.filter(**filters).first()


def parse_list(raw, limit):
    """Split a ``?v=`` value into at most ``limit`` codes, without repeats."""
    codes = []
    for code in (raw or "").split(","):
        code = code.strip()
        if code and code not in codes:
            codes.append(code)
    return codes[:limit]
