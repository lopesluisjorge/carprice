from django.db import models

# FIPE reserves the model year 32000 for brand-new (zero km) vehicles.
ZERO_KM_YEAR = 32000


class VehicleType(models.IntegerChoices):
    CAR = 1, "Carro"
    MOTORCYCLE = 2, "Moto"
    TRUCK = 3, "Caminhão"


class FuelType(models.IntegerChoices):
    """Codes taken from the FIPE year code (``"2026-5"`` -> Flex).

    FIPE may add codes without warning; an unknown one is stored as-is rather
    than coerced, so it shows up as a bare number instead of silently becoming
    gasoline.
    """

    GASOLINE = 1, "Gasolina"
    ETHANOL = 2, "Álcool"
    DIESEL = 3, "Diesel"
    ELECTRIC = 4, "Elétrico"
    FLEX = 5, "Flex"
    HYBRID = 6, "Híbrido"
    TETRAFUEL = 7, "Tetrafuel"


class ReferenceTable(models.Model):
    """A monthly FIPE price table."""

    fipe_code = models.PositiveIntegerField("código FIPE", unique=True)
    month = models.PositiveSmallIntegerField("mês")
    year = models.PositiveSmallIntegerField("ano")

    class Meta:
        verbose_name = "tabela de referência"
        verbose_name_plural = "tabelas de referência"
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(fields=["year", "month"], name="unique_reference_period"),
        ]

    def __str__(self):
        return f"{self.month:02d}/{self.year}"


class Brand(models.Model):
    fipe_code = models.PositiveIntegerField("código FIPE")
    name = models.CharField("nome", max_length=120)
    vehicle_type = models.PositiveSmallIntegerField(
        "tipo de veículo", choices=VehicleType.choices, default=VehicleType.CAR
    )

    class Meta:
        verbose_name = "marca"
        verbose_name_plural = "marcas"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle_type", "fipe_code"], name="unique_brand_per_vehicle_type"
            ),
        ]

    def __str__(self):
        return self.name


class VehicleModel(models.Model):
    brand = models.ForeignKey(
        Brand, on_delete=models.CASCADE, related_name="models", verbose_name="marca"
    )
    fipe_code = models.PositiveIntegerField("código FIPE")
    name = models.CharField("nome", max_length=200)

    class Meta:
        verbose_name = "modelo"
        verbose_name_plural = "modelos"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["brand", "fipe_code"], name="unique_model_per_brand"),
        ]

    def __str__(self):
        return f"{self.brand.name} {self.name}"


class ModelYear(models.Model):
    """A model/year/fuel variant — the unit FIPE actually prices."""

    vehicle_model = models.ForeignKey(
        VehicleModel,
        on_delete=models.CASCADE,
        related_name="model_years",
        verbose_name="modelo",
    )
    fipe_year_code = models.CharField("código ano FIPE", max_length=12)
    year = models.PositiveIntegerField("ano")
    fuel_type = models.PositiveSmallIntegerField(
        "combustível", choices=FuelType.choices, default=FuelType.GASOLINE
    )

    class Meta:
        verbose_name = "ano/modelo"
        verbose_name_plural = "anos/modelos"
        ordering = ["-year"]
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle_model", "fipe_year_code"], name="unique_year_per_model"
            ),
        ]

    @property
    def is_zero_km(self):
        return self.year == ZERO_KM_YEAR

    def __str__(self):
        label = "0 km" if self.is_zero_km else self.year
        return f"{self.vehicle_model} {label} {self.get_fuel_type_display()}"


class PriceQuote(models.Model):
    """The price of one ModelYear in one ReferenceTable. The largest table."""

    model_year = models.ForeignKey(
        ModelYear, on_delete=models.CASCADE, related_name="quotes", verbose_name="ano/modelo"
    )
    reference_table = models.ForeignKey(
        ReferenceTable,
        on_delete=models.CASCADE,
        related_name="quotes",
        verbose_name="tabela de referência",
    )
    value = models.DecimalField("valor", max_digits=12, decimal_places=2)
    fipe_code = models.CharField("código FIPE", max_length=20)
    # Recorded from the price payload itself, not inherited from ModelYear: it
    # is what FIPE actually priced, and it lets a quote be read on its own.
    fuel_type = models.PositiveSmallIntegerField(
        "combustível", choices=FuelType.choices, default=FuelType.GASOLINE
    )
    collected_at = models.DateTimeField("coletado em", auto_now=True)

    class Meta:
        verbose_name = "cotação"
        verbose_name_plural = "cotações"
        ordering = ["-reference_table__year", "-reference_table__month"]
        constraints = [
            models.UniqueConstraint(
                fields=["model_year", "reference_table"], name="unique_quote_per_reference"
            ),
        ]
        indexes = [
            models.Index(fields=["model_year", "reference_table"], name="quote_lookup_idx"),
        ]

    def __str__(self):
        return f"{self.model_year} — {self.reference_table}: {self.value}"


class QuoteLookupStatus(models.IntegerChoices):
    CREATED = 1, "Criada"
    UPDATED = 2, "Atualizada"
    NOT_FOUND = 3, "Sem cotação"


class QuoteLookup(models.Model):
    """What the last price request for one ModelYear in one ReferenceTable did.

    One row per pair, written only once the request resolves. NOT_FOUND is the
    part PriceQuote cannot express: FIPE lists year/fuel combinations it refuses
    to price, and without this row an absent quote and a never-asked pair look
    exactly alike.
    """

    model_year = models.ForeignKey(
        ModelYear, on_delete=models.CASCADE, related_name="lookups", verbose_name="ano/modelo"
    )
    reference_table = models.ForeignKey(
        ReferenceTable,
        on_delete=models.CASCADE,
        related_name="lookups",
        verbose_name="tabela de referência",
    )
    status = models.PositiveSmallIntegerField(
        "situação", choices=QuoteLookupStatus.choices, default=QuoteLookupStatus.CREATED
    )
    checked_at = models.DateTimeField("consultado em", auto_now=True)

    class Meta:
        verbose_name = "consulta de cotação"
        verbose_name_plural = "consultas de cotação"
        ordering = ["-checked_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["model_year", "reference_table"], name="unique_lookup_per_reference"
            ),
        ]
        indexes = [
            # "o que ficou sem cotação no mês X" — the reason this table exists.
            models.Index(fields=["reference_table", "status"], name="lookup_status_idx"),
        ]

    def __str__(self):
        return f"{self.model_year} — {self.reference_table}: {self.get_status_display()}"


class CrawlStatus(models.TextChoices):
    RUNNING = "running", "Em andamento"
    COMPLETED = "completed", "Concluída"
    FAILED = "failed", "Falhou"


class CrawlRun(models.Model):
    """One execution of the crawler over a reference table."""

    reference_table = models.ForeignKey(
        ReferenceTable,
        on_delete=models.CASCADE,
        related_name="crawl_runs",
        verbose_name="tabela de referência",
    )
    vehicle_type = models.PositiveSmallIntegerField(
        "tipo de veículo", choices=VehicleType.choices, default=VehicleType.CAR
    )
    status = models.CharField(
        "situação", max_length=12, choices=CrawlStatus.choices, default=CrawlStatus.RUNNING
    )
    started_at = models.DateTimeField("iniciada em", auto_now_add=True)
    finished_at = models.DateTimeField("finalizada em", null=True, blank=True)
    brands_done = models.PositiveIntegerField("marcas concluídas", default=0)
    models_done = models.PositiveIntegerField("modelos concluídos", default=0)
    quotes_created = models.PositiveIntegerField("cotações criadas", default=0)
    quotes_updated = models.PositiveIntegerField("cotações atualizadas", default=0)
    last_error = models.TextField("último erro", blank=True)

    class Meta:
        verbose_name = "execução do crawler"
        verbose_name_plural = "execuções do crawler"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.get_vehicle_type_display()} {self.reference_table} ({self.status})"


class CrawlCheckpoint(models.Model):
    """Per-brand progress marker so an interrupted run can resume."""

    crawl_run = models.ForeignKey(
        CrawlRun, on_delete=models.CASCADE, related_name="checkpoints", verbose_name="execução"
    )
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, verbose_name="marca")
    done = models.BooleanField("concluída", default=False)

    class Meta:
        verbose_name = "checkpoint"
        verbose_name_plural = "checkpoints"
        constraints = [
            models.UniqueConstraint(
                fields=["crawl_run", "brand"], name="unique_checkpoint_per_brand"
            ),
        ]

    def __str__(self):
        return f"{self.crawl_run_id}/{self.brand}: {'ok' if self.done else 'pendente'}"


class CollectionStatus(models.TextChoices):
    PENDING = "pending", "Agendada"
    RUNNING = "running", "Em andamento"
    PARTIAL = "partial", "Parcial"
    COMPLETED = "completed", "Concluída"
    FAILED = "failed", "Falhou"


class CollectionRequest(models.Model):
    """One on-demand collection, scheduled by a search.

    Holds no ReferenceTable foreign key on purpose: only FIPE knows which
    monthly tables exist, and the worker resolves periods when it runs.
    """

    term = models.CharField("termo buscado", max_length=200)
    vehicle_type = models.PositiveSmallIntegerField(
        "tipo de veículo", choices=VehicleType.choices, default=VehicleType.CAR
    )
    status = models.CharField(
        "situação",
        max_length=12,
        choices=CollectionStatus.choices,
        default=CollectionStatus.PENDING,
    )
    vehicle_models = models.ManyToManyField(
        VehicleModel,
        through="CollectionItem",
        related_name="collection_requests",
        verbose_name="modelos",
    )
    created_at = models.DateTimeField("criada em", auto_now_add=True)
    started_at = models.DateTimeField("iniciada em", null=True, blank=True)
    finished_at = models.DateTimeField("finalizada em", null=True, blank=True)
    models_done = models.PositiveIntegerField("modelos concluídos", default=0)
    quotes_created = models.PositiveIntegerField("cotações criadas", default=0)
    quotes_updated = models.PositiveIntegerField("cotações atualizadas", default=0)
    quotes_missing = models.PositiveIntegerField("sem cotação na FIPE", default=0)
    requests_spent = models.PositiveIntegerField("requisições gastas", default=0)
    last_error = models.TextField("último erro", blank=True)

    class Meta:
        verbose_name = "coleta sob demanda"
        verbose_name_plural = "coletas sob demanda"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.term} ({self.status})"

    @property
    def models_total(self):
        # Not a stored counter: a redundant one could only drift from the rows.
        return self.items.count()


class CollectionItem(models.Model):
    """One model inside a request. The through table of the M2M."""

    request = models.ForeignKey(
        CollectionRequest,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="coleta",
    )
    vehicle_model = models.ForeignKey(
        VehicleModel, on_delete=models.CASCADE, verbose_name="modelo"
    )
    # Position in the full-text ranking: 0 is the most relevant.
    rank = models.PositiveIntegerField("relevância", default=0)
    status = models.CharField(
        "situação",
        max_length=12,
        choices=CollectionStatus.choices,
        default=CollectionStatus.PENDING,
    )
    finished_at = models.DateTimeField("finalizado em", null=True, blank=True)

    class Meta:
        verbose_name = "item de coleta"
        verbose_name_plural = "itens de coleta"
        ordering = ["rank", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["request", "vehicle_model"], name="unique_model_per_collection"
            ),
        ]

    def __str__(self):
        return f"{self.request_id}/{self.vehicle_model}: {self.status}"
