"""The FTS5 index over vehicle model names.

The table is standalone and not ``content='crawler_vehiclemodel'`` because
``brand`` is a foreign key there, not a column: with external content FTS5 would
issue ``SELECT name, brand FROM crawler_vehiclemodel`` and fail.

Triggers rather than Django signals because they hold for every write — a future
``bulk_create`` fires no signal but does fire a trigger.
"""

from django.db import migrations

CREATE_TABLE = """
CREATE VIRTUAL TABLE vehicle_model_fts USING fts5(
    name,
    brand,
    tokenize="unicode61 remove_diacritics 2"
)
"""

INSERT_TRIGGER = """
CREATE TRIGGER vehicle_model_fts_insert AFTER INSERT ON crawler_vehiclemodel BEGIN
    INSERT INTO vehicle_model_fts(rowid, name, brand)
    VALUES (new.id, new.name, (SELECT name FROM crawler_brand WHERE id = new.brand_id));
END
"""

UPDATE_TRIGGER = """
CREATE TRIGGER vehicle_model_fts_update AFTER UPDATE ON crawler_vehiclemodel BEGIN
    DELETE FROM vehicle_model_fts WHERE rowid = old.id;
    INSERT INTO vehicle_model_fts(rowid, name, brand)
    VALUES (new.id, new.name, (SELECT name FROM crawler_brand WHERE id = new.brand_id));
END
"""

DELETE_TRIGGER = """
CREATE TRIGGER vehicle_model_fts_delete AFTER DELETE ON crawler_vehiclemodel BEGIN
    DELETE FROM vehicle_model_fts WHERE rowid = old.id;
END
"""

# sync.py does update_or_create on Brand precisely to pick up a FIPE rename, so
# without this the index would keep serving the old brand name.
BRAND_TRIGGER = """
CREATE TRIGGER vehicle_model_fts_brand_update AFTER UPDATE ON crawler_brand BEGIN
    DELETE FROM vehicle_model_fts
    WHERE rowid IN (SELECT id FROM crawler_vehiclemodel WHERE brand_id = new.id);
    INSERT INTO vehicle_model_fts(rowid, name, brand)
    SELECT id, name, new.name FROM crawler_vehiclemodel WHERE brand_id = new.id;
END
"""

BACKFILL = """
INSERT INTO vehicle_model_fts(rowid, name, brand)
SELECT vm.id, vm.name, b.name
FROM crawler_vehiclemodel vm
JOIN crawler_brand b ON b.id = vm.brand_id
"""


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("crawler", "0005_alter_modelyear_fuel_type_alter_pricequote_fuel_type"),
    ]

    operations = [
        # A list, not one string: the sqlite3 driver runs a single statement per
        # execute(), so a single blob would silently apply only the first CREATE.
        migrations.RunSQL(
            sql=[
                CREATE_TABLE,
                INSERT_TRIGGER,
                UPDATE_TRIGGER,
                DELETE_TRIGGER,
                BRAND_TRIGGER,
                BACKFILL,
            ],
            reverse_sql=[
                "DROP TRIGGER IF EXISTS vehicle_model_fts_brand_update",
                "DROP TRIGGER IF EXISTS vehicle_model_fts_delete",
                "DROP TRIGGER IF EXISTS vehicle_model_fts_update",
                "DROP TRIGGER IF EXISTS vehicle_model_fts_insert",
                "DROP TABLE IF EXISTS vehicle_model_fts",
            ],
        ),
    ]
