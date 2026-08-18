"""Classifies every model already stored, by reading its own name.

The engine size was never collected — FIPE has no such field — so the only
source is the model name itself, which is as available now as it was when the
row was written. Hence a backfill and not a re-crawl: no request is made.

``crawler.engines`` is imported directly, unlike the models: it is a pure
parser over strings, and the only thing it takes from ``crawler.models`` is the
FuelType codes, which are FIPE's numbers and do not change with the schema.
"""

from django.db import migrations

from crawler import engines

BATCH = 500


def classify(apps, schema_editor):
    EngineType = apps.get_model('crawler', 'EngineType')
    VehicleModel = apps.get_model('crawler', 'VehicleModel')

    types = {}
    updated = []
    for model in VehicleModel.objects.prefetch_related('model_years').iterator(BATCH):
        value = engines.classify(model.name, [y.fuel_type for y in model.model_years.all()])
        if value not in types:
            types[value], _ = EngineType.objects.get_or_create(
                value=value, defaults={'description': engines.describe(value)}
            )
        model.engine_type = types[value]
        updated.append(model)
        if len(updated) >= BATCH:
            VehicleModel.objects.bulk_update(updated, ['engine_type'])
            updated = []
    if updated:
        VehicleModel.objects.bulk_update(updated, ['engine_type'])


def unclassify(apps, schema_editor):
    """Reversible: the classification is derived, so dropping it loses nothing.

    The EngineType rows go too — with a PROTECTed foreign key, leaving them
    behind would block a later reverse of 0008.
    """
    VehicleModel = apps.get_model('crawler', 'VehicleModel')
    EngineType = apps.get_model('crawler', 'EngineType')
    VehicleModel.objects.update(engine_type=None)
    EngineType.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('crawler', '0008_engine_type'),
    ]

    operations = [
        migrations.RunPython(classify, unclassify),
    ]
