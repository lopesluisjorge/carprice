"""Full-text search over vehicle model names — the only raw SQL in the project.

Two dialects live here and nowhere else: callers ask for ranked ids and never see
MATCH, bm25, tsquery or ts_rank. Which one runs is decided by the connection, so
the screens do not know or care which database is underneath.

The tokenizing is shared, and that is what makes both branches safe: a token is
letters and digits only, so nothing the user types can ever reach the query as
syntax. FTS5 would read a bare ``AND``, ``-`` or ``*`` as an operator; Postgres
would read ``&``, ``|``, ``!`` or ``:`` the same way. Neither can survive the
tokenizer, and each branch quotes what is left on top of that.
"""

import re

from django.db import connection

TABLE = "vehicle_model_fts"

# The text search configuration built by web/migrations/0001: `simple` plus the
# unaccent dictionary. Naming it here is what strips accents off the typed term,
# with the same dictionary that stripped them off the stored vector.
CONFIG = "simple_unaccent"

# Name outweighs brand: "fiat" must not drown the results in 194 Fiat models.
NAME_WEIGHT = 10.0
BRAND_WEIGHT = 1.0

# ts_rank takes the four label weights in the order {D, C, B, A}. The migration
# labels the model name 'A' and the brand 'B'; D and C are unused. Same 10:1
# ratio as the bm25 weights above, in the 0..1 scale ts_rank expects.
PG_RANK_WEIGHTS = [0.0, 0.0, BRAND_WEIGHT / NAME_WEIGHT, 1.0]

# Letters and digits, accents included; both engines strip the accents on either
# side of the comparison, so "citroen" still finds "Citroën".
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(term):
    """The searchable words in what the user typed.

    Single characters are dropped when there is anything longer: "gol 1.0"
    tokenizes as gol/1/0, and the loose digits are only noise. A term that is
    nothing but single characters keeps them, so "c" still searches.
    """
    tokens = TOKEN.findall(term or "")
    longer = [token for token in tokens if len(token) > 1]
    return longer or tokens


def build_match_query(term):
    """What the user typed, as FTS5 syntax, with its operators defused.

    Each token becomes a quoted literal with a prefix star, joined by AND:
    ``corsa sedan`` -> ``"corsa"* AND "sedan"*``. The quotes are what turn a
    typed ``AND``, ``-`` or ``*`` into ordinary text instead of syntax.
    """
    return " AND ".join(f'"{token}"*' for token in tokenize(term))


def build_tsquery(term):
    """The same thing in Postgres syntax: ``corsa:* & sedan:*``.

    ``:*`` is the prefix match and ``&`` is the AND, matching the FTS5 branch
    meaning for meaning. The accents come off in SQL, by the same ``unaccent``
    the migration used to build the stored vector.
    """
    return " & ".join(f"{token}:*" for token in tokenize(term))


def _search_sqlite(term):
    # The weights are module constants, never user input, so they can be inlined.
    # rowid breaks ties: without it two equally ranked models could swap places
    # between requests, and the paginator would show one twice and the other
    # never.
    sql = (
        f"SELECT rowid FROM {TABLE} WHERE {TABLE} MATCH %s "
        f"ORDER BY bm25({TABLE}, {NAME_WEIGHT}, {BRAND_WEIGHT}), rowid"
    )
    return sql, [build_match_query(term)]


def _search_postgres(term):
    # bm25 is "lower is better", ts_rank is the opposite — hence DESC here.
    # Normalization 1 divides by 1 + log(length), which is what keeps a long
    # model name from outranking a short one just by having more words in it;
    # bm25 does that length correction on its own.
    sql = (
        f"SELECT rowid FROM {TABLE} "
        f"WHERE document @@ to_tsquery('{CONFIG}', %s) "
        f"ORDER BY ts_rank(%s::real[], document, to_tsquery('{CONFIG}', %s), 1) DESC, rowid"
    )
    query = build_tsquery(term)
    return sql, [query, PG_RANK_WEIGHTS, query]


def search(term):
    """Ids of the VehicleModels matching `term`, best first.

    Returns None when there is no usable term, which is not the same as []:
    None means "no search", [] means "searched and found nothing".
    """
    if not tokenize(term):
        return None
    build = _search_postgres if connection.vendor == "postgresql" else _search_sqlite
    sql, params = build(term)
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [row[0] for row in cursor.fetchall()]
