"""A FipeClient stand-in backed by recorded fixtures. No test touches the network."""

import json
from decimal import Decimal
from pathlib import Path

from crawler.fipe import FipeNotFound

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeFipeClient:
    """Serves the fixtures and records every call, so tests can assert on traffic.

    ``missing`` holds ``fipe_year_code`` values that should raise FipeNotFound,
    mirroring the year/fuel combinations FIPE lists but cannot price.
    """

    def __init__(self, missing=(), price_value="R$ 22.431,00"):
        self.missing = set(missing)
        self.price_value = price_value
        self.calls = []

    def reference_tables(self):
        self.calls.append(("reference_tables",))
        return load("reference_tables.json")

    def brands(self, reference_code, vehicle_type):
        self.calls.append(("brands", reference_code, vehicle_type))
        return load("brands.json")

    def models(self, reference_code, vehicle_type, brand_code):
        self.calls.append(("models", brand_code))
        if brand_code != 21:
            return []
        return load("models_21.json")["Modelos"]

    def model_years(self, reference_code, vehicle_type, brand_code, model_code):
        self.calls.append(("model_years", model_code))
        return load(f"model_years_{model_code}.json")

    def price(self, reference_code, vehicle_type, brand_code, model_code, fipe_year_code):
        self.calls.append(("price", model_code, fipe_year_code))
        if fipe_year_code in self.missing:
            raise FipeNotFound("nenhum veículo encontrado")
        payload = load("price.json")
        payload["Valor"] = self.price_value
        payload["AnoModelo"] = int(fipe_year_code.split("-")[0])
        return payload

    def count(self, endpoint):
        return sum(1 for call in self.calls if call[0] == endpoint)


def money(value):
    return Decimal(value)
