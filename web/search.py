"""Full-text search over vehicle model names — the only raw SQL in the project.

SQLite's FTS5 lives here and nowhere else: callers ask for ranked ids and never
see MATCH or bm25. Moving to Postgres means rewriting this module and one
migration, not the screens.
"""

import re

from django.db import connection

TABLE = "vehicle_model_fts"

# Name outweighs brand: "fiat" must not drown the results in 194 Fiat models.
NAME_WEIGHT = 10.0
BRAND_WEIGHT = 1.0

# Letters and digits, accents included; the FTS tokenizer strips the accents on
# both sides, so "citroen" still finds "Citroën".
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def build_match_query(term):
    """What the user typed, as FTS5 syntax, with its operators defused.

    Each token becomes a quoted literal with a prefix star, joined by AND:
    ``corsa sedan`` -> ``"corsa"* AND "sedan"*``. The quotes are what turn a
    typed ``AND``, ``-`` or ``*`` into ordinary text instead of syntax.
    """
    tokens = TOKEN.findall(term or "")
    longer = [token for token in tokens if len(token) > 1]
    return " AND ".join(f'"{token}"*' for token in (longer or tokens))


def search(term):
    """Ids of the VehicleModels matching `term`, best first.

    Returns None when there is no usable term, which is not the same as []:
    None means "no search", [] means "searched and found nothing".
    """
    match = build_match_query(term)
    if not match:
        return None
    # The weights are module constants, never user input, so they can be inlined.
    sql = (
        f"SELECT rowid FROM {TABLE} WHERE {TABLE} MATCH %s "
        f"ORDER BY bm25({TABLE}, {NAME_WEIGHT}, {BRAND_WEIGHT})"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, [match])
        return [row[0] for row in cursor.fetchall()]
