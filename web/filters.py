"""The search querystring, in and out.

Deliberately free of model imports: the screen's whole notion of "what was
asked" is a plain dataclass, testable without a database.
"""

import dataclasses
from decimal import Decimal
from urllib.parse import urlencode

# Operator -> ORM lookup. The 0 km year (32000) needs no special case: `gte`
# includes it, `eq` and `lte` exclude it, which is exactly the intended meaning.
YEAR_LOOKUPS = {"gte": "year__gte", "eq": "year", "lte": "year__lte"}
DEFAULT_YEAR_OP = "gte"

# These land on PriceQuote.value, not on ModelYear like the year ones. The module
# still imports no models, so they are only strings — queries.py is what knows
# which queryset each one belongs to.
PRICE_LOOKUPS = {"gte": "value__gte", "lte": "value__lte"}
# "até", unlike the year's "a partir de": price is searched as a budget ceiling.
DEFAULT_PRICE_OP = "lte"

# There is no "exatamente" for price on purpose: with fixed steps it would match
# only the exact amount — almost nothing — and read as a bug.

# Reais, not thousands. Only the label on screen abbreviates.
PRICE_STEPS = [10_000, 20_000, 30_000, 50_000, 75_000, 100_000, 150_000, 200_000]

SORTS = ("price_asc", "price_desc")


# The engine type travels as its own value ("1.4", "-1"), not as a row id: like
# the FIPE codes, it keeps a shared link meaningful in another database.
MAX_ENGINE_VALUE = Decimal("99.9")


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _engine(value):
    """A displacement from the querystring, or None.

    Decimal() accepts "NaN" and "Infinity" and refuses nothing about size, so
    neither the range nor the finiteness check is decoration: both reach the
    column, and the column is numeric(3, 1).
    """
    try:
        number = Decimal(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    if not number.is_finite() or abs(number) > MAX_ENGINE_VALUE:
        return None
    return number


@dataclasses.dataclass(frozen=True)
class SearchFilters:
    term: str = ""
    brand: str = ""
    fuels: tuple = ()
    engine: Decimal | None = None
    year_op: str = DEFAULT_YEAR_OP
    year: int | None = None
    price_op: str = DEFAULT_PRICE_OP
    price: int | None = None
    sort: str = ""
    page: int = 1

    @classmethod
    def from_query(cls, query):
        fuels = []
        for raw in query.getlist("fuel"):
            code = _int(raw)
            if code is not None and code not in fuels:
                fuels.append(code)
        year_op = query.get("year_op", DEFAULT_YEAR_OP)
        price_op = query.get("price_op", DEFAULT_PRICE_OP)
        price = _int(query.get("price"))
        sort = query.get("sort", "")
        page = _int(query.get("page"), 1)
        return cls(
            term=query.get("q", "").strip(),
            brand=query.get("brand", "").strip(),
            fuels=tuple(fuels),
            engine=_engine(query.get("engine")),
            year_op=year_op if year_op in YEAR_LOOKUPS else DEFAULT_YEAR_OP,
            year=_int(query.get("year")),
            price_op=price_op if price_op in PRICE_LOOKUPS else DEFAULT_PRICE_OP,
            # A step that is not on the list is kept: a shared URL has to come
            # back showing what it was sharing.
            price=price if price and price > 0 else None,
            sort=sort if sort in SORTS else "",
            page=page if page and page > 0 else 1,
        )

    @property
    def is_empty(self):
        """Whether anything was actually asked. Sorting does not count — it
        narrows nothing, and the empty state has to keep saying "collect data"
        instead of "loosen the filters"."""
        return (
            not self.term
            and not self.brand
            and not self.fuels
            and self.engine is None
            and self.year is None
            and self.price is None
        )

    @property
    def year_lookup(self):
        return YEAR_LOOKUPS[self.year_op]

    @property
    def price_lookup(self):
        return PRICE_LOOKUPS[self.price_op]

    def querystring(self, **overrides):
        """The link back to this search, optionally with a field replaced."""
        fields = {
            "q": self.term,
            "brand": self.brand,
            "fuel": list(self.fuels),
            "engine": self.engine,
            "year_op": self.year_op if self.year is not None else "",
            "year": self.year,
            "price_op": self.price_op if self.price is not None else "",
            "price": self.price,
            "sort": self.sort,
            "page": self.page,
        }
        fields |= overrides
        if fields.get("page") == 1:
            fields["page"] = None
        pairs = []
        for key, value in fields.items():
            if value is None or value == "" or value == []:
                continue
            if isinstance(value, (list, tuple)):
                pairs.extend((key, item) for item in value)
            else:
                pairs.append((key, value))
        return urlencode(pairs)
