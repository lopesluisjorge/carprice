"""The search querystring, in and out.

Deliberately free of model imports: the screen's whole notion of "what was
asked" is a plain dataclass, testable without a database.
"""

import dataclasses
from urllib.parse import urlencode

# Operator -> ORM lookup. The 0 km year (32000) needs no special case: `gte`
# includes it, `eq` and `lte` exclude it, which is exactly the intended meaning.
YEAR_LOOKUPS = {"gte": "year__gte", "eq": "year", "lte": "year__lte"}
DEFAULT_YEAR_OP = "gte"


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclasses.dataclass(frozen=True)
class SearchFilters:
    term: str = ""
    fuels: tuple = ()
    year_op: str = DEFAULT_YEAR_OP
    year: int | None = None
    page: int = 1

    @classmethod
    def from_query(cls, query):
        fuels = []
        for raw in query.getlist("fuel"):
            code = _int(raw)
            if code is not None and code not in fuels:
                fuels.append(code)
        year_op = query.get("year_op", DEFAULT_YEAR_OP)
        page = _int(query.get("page"), 1)
        return cls(
            term=query.get("q", "").strip(),
            fuels=tuple(fuels),
            year_op=year_op if year_op in YEAR_LOOKUPS else DEFAULT_YEAR_OP,
            year=_int(query.get("year")),
            page=page if page and page > 0 else 1,
        )

    @property
    def is_empty(self):
        return not self.term and not self.fuels and self.year is None

    @property
    def year_lookup(self):
        return YEAR_LOOKUPS[self.year_op]

    def querystring(self, **overrides):
        """The link back to this search, optionally with a field replaced."""
        fields = {
            "q": self.term,
            "fuel": list(self.fuels),
            "year_op": self.year_op if self.year is not None else "",
            "year": self.year,
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
