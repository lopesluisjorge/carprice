from django.contrib import admin

from crawler.models import (
    Brand,
    CrawlCheckpoint,
    CrawlRun,
    ModelYear,
    PriceQuote,
    QuoteLookup,
    ReferenceTable,
    VehicleModel,
)


@admin.register(ReferenceTable)
class ReferenceTableAdmin(admin.ModelAdmin):
    list_display = ["__str__", "fipe_code"]
    search_fields = ["year"]


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ["name", "vehicle_type", "fipe_code"]
    list_filter = ["vehicle_type"]
    search_fields = ["name"]


@admin.register(VehicleModel)
class VehicleModelAdmin(admin.ModelAdmin):
    list_display = ["name", "brand", "fipe_code"]
    list_filter = ["brand__vehicle_type"]
    search_fields = ["name"]
    autocomplete_fields = ["brand"]


@admin.register(ModelYear)
class ModelYearAdmin(admin.ModelAdmin):
    list_display = ["__str__", "year", "fuel_type"]
    list_filter = ["fuel_type"]
    search_fields = ["vehicle_model__name"]


@admin.register(PriceQuote)
class PriceQuoteAdmin(admin.ModelAdmin):
    list_display = ["model_year", "reference_table", "value", "fuel_type", "fipe_code"]
    list_filter = ["reference_table", "fuel_type"]
    search_fields = ["fipe_code", "model_year__vehicle_model__name"]


@admin.register(QuoteLookup)
class QuoteLookupAdmin(admin.ModelAdmin):
    list_display = ["model_year", "reference_table", "status", "checked_at"]
    list_filter = ["status", "reference_table"]
    search_fields = ["model_year__vehicle_model__name"]


class CrawlCheckpointInline(admin.TabularInline):
    model = CrawlCheckpoint
    extra = 0
    can_delete = False


@admin.register(CrawlRun)
class CrawlRunAdmin(admin.ModelAdmin):
    list_display = [
        "reference_table",
        "vehicle_type",
        "status",
        "started_at",
        "quotes_created",
        "quotes_updated",
    ]
    list_filter = ["status", "vehicle_type"]
    inlines = [CrawlCheckpointInline]
