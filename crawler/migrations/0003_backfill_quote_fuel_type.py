"""Repairs quotes left as gasoline by the first backfill.

Migration 0002 only knew the fuel codes 1-3, so quotes of electric (4) and flex
(5) vehicles kept the field default. This re-runs the corrected backfill. Only
rows still holding the default are touched, so fuel already collected from a
price payload is never overwritten.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    PriceQuote = apps.get_model('crawler', 'PriceQuote')
    ModelYear = apps.get_model('crawler', 'ModelYear')
    known = ModelYear.objects.exclude(fuel_type=1).values_list('fuel_type', flat=True).distinct()
    for fuel_type in known:
        PriceQuote.objects.filter(
            fuel_type=1, model_year__fuel_type=fuel_type
        ).update(fuel_type=fuel_type)


class Migration(migrations.Migration):

    dependencies = [
        ('crawler', '0002_pricequote_fuel_type'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
