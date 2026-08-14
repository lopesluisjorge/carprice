"""The full-text index over vehicle model names, one branch per engine.

Both branches build the same thing: a standalone ``vehicle_model_fts`` table
keyed by the VehicleModel id, kept in sync by triggers. It is not FTS5's
``content='crawler_vehiclemodel'`` (nor a column on that table) because ``brand``
is a foreign key there, not a column: with external content FTS5 would issue
``SELECT name, brand FROM crawler_vehiclemodel`` and fail, and on either engine
the index has to carry the brand *name*, which lives one join away.

Triggers rather than Django signals because they hold for every write — a future
``bulk_create`` fires no signal but does fire a trigger.

The two dialects diverge in what they can express, not in what they mean:

- SQLite stores the text and lets FTS5 tokenize at query time; Postgres stores a
  precomputed ``tsvector`` and indexes it with GIN.
- FTS5 strips accents with ``remove_diacritics 2``; Postgres needs the
  ``unaccent`` extension, applied when the vector is built and again on the
  query side (see ``web/search.py``).
- ``simple``, not ``portuguese``: model names are not prose. Stemming
  "Mille" or "TETRAFUEL" as Portuguese words would only distort them.
"""

from django.db import migrations

SQLITE_FORWARD = [
    """
    CREATE VIRTUAL TABLE vehicle_model_fts USING fts5(
        name,
        brand,
        tokenize="unicode61 remove_diacritics 2"
    )
    """,
    """
    CREATE TRIGGER vehicle_model_fts_insert AFTER INSERT ON crawler_vehiclemodel BEGIN
        INSERT INTO vehicle_model_fts(rowid, name, brand)
        VALUES (new.id, new.name, (SELECT name FROM crawler_brand WHERE id = new.brand_id));
    END
    """,
    """
    CREATE TRIGGER vehicle_model_fts_update AFTER UPDATE ON crawler_vehiclemodel BEGIN
        DELETE FROM vehicle_model_fts WHERE rowid = old.id;
        INSERT INTO vehicle_model_fts(rowid, name, brand)
        VALUES (new.id, new.name, (SELECT name FROM crawler_brand WHERE id = new.brand_id));
    END
    """,
    """
    CREATE TRIGGER vehicle_model_fts_delete AFTER DELETE ON crawler_vehiclemodel BEGIN
        DELETE FROM vehicle_model_fts WHERE rowid = old.id;
    END
    """,
    # sync.py does update_or_create on Brand precisely to pick up a FIPE rename,
    # so without this the index would keep serving the old brand name.
    """
    CREATE TRIGGER vehicle_model_fts_brand_update AFTER UPDATE ON crawler_brand BEGIN
        DELETE FROM vehicle_model_fts
        WHERE rowid IN (SELECT id FROM crawler_vehiclemodel WHERE brand_id = new.id);
        INSERT INTO vehicle_model_fts(rowid, name, brand)
        SELECT id, name, new.name FROM crawler_vehiclemodel WHERE brand_id = new.id;
    END
    """,
    """
    INSERT INTO vehicle_model_fts(rowid, name, brand)
    SELECT vm.id, vm.name, b.name
    FROM crawler_vehiclemodel vm
    JOIN crawler_brand b ON b.id = vm.brand_id
    """,
]

SQLITE_REVERSE = [
    "DROP TRIGGER IF EXISTS vehicle_model_fts_brand_update",
    "DROP TRIGGER IF EXISTS vehicle_model_fts_delete",
    "DROP TRIGGER IF EXISTS vehicle_model_fts_update",
    "DROP TRIGGER IF EXISTS vehicle_model_fts_insert",
    "DROP TABLE IF EXISTS vehicle_model_fts",
]

# Kept in sync by hand with web/search.py, which names the same configuration on
# the query side. Migrations must not import app code — that is why it is a
# literal in both places instead of a shared constant.
CONFIG = "simple_unaccent"


# Weights live in the vector, not in the query: 'A' is the model name, 'B' the
# brand. web/search.py decides how much each one counts when it ranks.
def _document(name, brand):
    return (
        f"setweight(to_tsvector('{CONFIG}', {name}), 'A') || "
        f"setweight(to_tsvector('{CONFIG}', {brand}), 'B')"
    )


POSTGRES_FORWARD = [
    # Trusted since Postgres 13, so the database owner can create it without
    # being superuser. A managed instance that forbids it needs it installed by
    # hand before this migration runs.
    "CREATE EXTENSION IF NOT EXISTS unaccent",
    # Accent folding belongs in the configuration, not at the call sites. Both
    # to_tsvector and to_tsquery run the same dictionaries, so naming this
    # configuration strips the accents on the stored side and on the typed side
    # — which is exactly what FTS5's `remove_diacritics 2` does over there.
    # Calling unaccent() inline would work but has to be repeated everywhere,
    # and unaccent() is STABLE, so it could never move into an index expression.
    f"CREATE TEXT SEARCH CONFIGURATION {CONFIG} (COPY = simple)",
    f"""
    ALTER TEXT SEARCH CONFIGURATION {CONFIG}
    ALTER MAPPING FOR hword, hword_part, word WITH unaccent, simple
    """,
    """
    CREATE TABLE vehicle_model_fts (
        rowid integer PRIMARY KEY,
        document tsvector NOT NULL
    )
    """,
    "CREATE INDEX vehicle_model_fts_document ON vehicle_model_fts USING GIN (document)",
    # One function for both INSERT and UPDATE: the row is replaced either way,
    # which is also what the SQLite update trigger does.
    f"""
    CREATE FUNCTION vehicle_model_fts_refresh() RETURNS trigger AS $$
    BEGIN
        DELETE FROM vehicle_model_fts WHERE rowid = NEW.id;
        INSERT INTO vehicle_model_fts (rowid, document)
        SELECT NEW.id, {_document("NEW.name", "b.name")}
        FROM crawler_brand b WHERE b.id = NEW.brand_id;
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE FUNCTION vehicle_model_fts_remove() RETURNS trigger AS $$
    BEGIN
        DELETE FROM vehicle_model_fts WHERE rowid = OLD.id;
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql
    """,
    f"""
    CREATE FUNCTION vehicle_model_fts_brand_refresh() RETURNS trigger AS $$
    BEGIN
        DELETE FROM vehicle_model_fts
        WHERE rowid IN (SELECT id FROM crawler_vehiclemodel WHERE brand_id = NEW.id);
        INSERT INTO vehicle_model_fts (rowid, document)
        SELECT vm.id, {_document("vm.name", "NEW.name")}
        FROM crawler_vehiclemodel vm WHERE vm.brand_id = NEW.id;
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER vehicle_model_fts_insert AFTER INSERT ON crawler_vehiclemodel
    FOR EACH ROW EXECUTE FUNCTION vehicle_model_fts_refresh()
    """,
    """
    CREATE TRIGGER vehicle_model_fts_update AFTER UPDATE ON crawler_vehiclemodel
    FOR EACH ROW EXECUTE FUNCTION vehicle_model_fts_refresh()
    """,
    """
    CREATE TRIGGER vehicle_model_fts_delete AFTER DELETE ON crawler_vehiclemodel
    FOR EACH ROW EXECUTE FUNCTION vehicle_model_fts_remove()
    """,
    """
    CREATE TRIGGER vehicle_model_fts_brand_update AFTER UPDATE ON crawler_brand
    FOR EACH ROW EXECUTE FUNCTION vehicle_model_fts_brand_refresh()
    """,
    f"""
    INSERT INTO vehicle_model_fts (rowid, document)
    SELECT vm.id, {_document("vm.name", "b.name")}
    FROM crawler_vehiclemodel vm
    JOIN crawler_brand b ON b.id = vm.brand_id
    """,
]

POSTGRES_REVERSE = [
    "DROP TRIGGER IF EXISTS vehicle_model_fts_brand_update ON crawler_brand",
    "DROP TRIGGER IF EXISTS vehicle_model_fts_delete ON crawler_vehiclemodel",
    "DROP TRIGGER IF EXISTS vehicle_model_fts_update ON crawler_vehiclemodel",
    "DROP TRIGGER IF EXISTS vehicle_model_fts_insert ON crawler_vehiclemodel",
    "DROP FUNCTION IF EXISTS vehicle_model_fts_brand_refresh()",
    "DROP FUNCTION IF EXISTS vehicle_model_fts_remove()",
    "DROP FUNCTION IF EXISTS vehicle_model_fts_refresh()",
    "DROP TABLE IF EXISTS vehicle_model_fts",
    f"DROP TEXT SEARCH CONFIGURATION IF EXISTS {CONFIG}",
]


def _run(schema_editor, sqlite_statements, postgres_statements):
    """One statement per execute(): the sqlite3 driver runs only the first of a
    multi-statement string, silently, and the same discipline keeps the Postgres
    branch readable."""
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        statements = postgres_statements
    elif vendor == "sqlite":
        statements = sqlite_statements
    else:
        raise NotImplementedError(f"No full-text index defined for {vendor}.")
    for statement in statements:
        # params=None so the driver leaves any % in the SQL alone.
        schema_editor.execute(statement, params=None)


def forwards(apps, schema_editor):
    _run(schema_editor, SQLITE_FORWARD, POSTGRES_FORWARD)


def backwards(apps, schema_editor):
    _run(schema_editor, SQLITE_REVERSE, POSTGRES_REVERSE)


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("crawler", "0005_alter_modelyear_fuel_type_alter_pricequote_fuel_type"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
