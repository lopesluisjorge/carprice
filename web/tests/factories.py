"""Test data builders, shared by the web test modules.

The name deliberately does not start with `test`, so the runner does not
collect it as a test module.
"""

from decimal import Decimal

from crawler.models import Brand
from crawler.models import FuelType
from crawler.models import ModelYear
from crawler.models import PriceQuote
from crawler.models import ReferenceTable
from crawler.models import VehicleModel


def build_vehicle(brand_code=21, model_code=4712, year=2017, fuel=FuelType.FLEX):
    brand, _ = Brand.objects.get_or_create(fipe_code=brand_code, defaults={"name": "Fiat"})
    vehicle_model, _ = VehicleModel.objects.get_or_create(
        brand=brand, fipe_code=model_code, defaults={"name": "500 Cult 1.4"}
    )
    return ModelYear.objects.create(
        vehicle_model=vehicle_model,
        fipe_year_code=f"{year}-{int(fuel)}",
        year=year,
        fuel_type=fuel,
    )


def add_quote(model_year, year, month, value):
    reference_table, _ = ReferenceTable.objects.get_or_create(
        year=year, month=month, defaults={"fipe_code": year * 100 + month}
    )
    return PriceQuote.objects.create(
        model_year=model_year,
        reference_table=reference_table,
        value=Decimal(value),
        fipe_code="001124-0",
        fuel_type=model_year.fuel_type,
    )
