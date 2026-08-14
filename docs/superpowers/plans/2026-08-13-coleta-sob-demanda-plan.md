# Coleta sob demanda — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Buscar por um veículo agenda a coleta do histórico daqueles modelos, executada em background por um worker próprio dentro da cota da FIPE.

**Architecture:** A view grava a intenção (`CollectionRequest` + um `CollectionItem` por modelo) via `crawler/services/scheduling.py`, que só usa o ORM — a fronteira "`web` nunca chama a FIPE" vira "`web` escreve pedido, nunca executa coleta". Um management command separado (`process_crawl_queue`) consome a fila com `crawler/services/collecting.py`, protegido por um lock de arquivo para que exista uma única janela de cota.

**Tech Stack:** Django 6.1, Python 3.14, SQLite, `fcntl` da stdlib para o lock. Zero dependências além do Django.

Spec: [`../specs/2026-08-13-coleta-sob-demanda-design.md`](../specs/2026-08-13-coleta-sob-demanda-design.md)

---

## Estrutura de arquivos

| arquivo | responsabilidade |
|---|---|
| `crawler/models.py` (mod) | `CollectionRequest`, `CollectionItem`, seus status |
| `crawler/migrations/0006_collection_queue.py` (novo) | as duas tabelas |
| `crawler/services/scheduling.py` (novo) | **sem FIPE**: `periods_for`, dedup por cobertura, `request_collection` |
| `crawler/services/collecting.py` (novo) | o motor do worker: consome um pedido gastando orçamento |
| `crawler/services/sync.py` (mod) | extrai `upsert_quote` reutilizável, sem `CrawlRun` |
| `crawler/management/commands/process_crawl_queue.py` (novo) | lock, laço de passadas, relatório |
| `web/views.py` (mod) | dispara o agendamento quando há termo |
| `web/templates/web/partials/collection_status.html` (novo) | a faixa de estado |
| `crawler/tests/test_scheduling.py` (novo) | `periods_for` e dedup |
| `crawler/tests/test_collecting.py` (novo) | worker, orçamento, ordem, lock |
| `web/tests/test_boundary.py` (novo) | `web` não importa `FipeClient` |

**Validado antes deste plano, numa sessão à parte:** a aritmética de meses (versão 2015 → 13 períodos; janeiro/2026 → 3 meses atrás = outubro/2025) e o `fcntl.flock` não-bloqueante (levanta `BlockingIOError`; o SO libera na morte do processo).

---

## Task 1: Tabelas da fila

**Files:**
- Modify: `crawler/models.py` (fim do arquivo)
- Create: `crawler/migrations/0006_collection_queue.py` (gerada)

- [ ] **Step 1: Escrever os models**

No fim de `crawler/models.py`:

```python
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
        "situação", max_length=12, choices=CollectionStatus.choices,
        default=CollectionStatus.PENDING,
    )
    vehicle_models = models.ManyToManyField(
        VehicleModel, through="CollectionItem", related_name="collection_requests",
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
        CollectionRequest, on_delete=models.CASCADE, related_name="items",
        verbose_name="coleta",
    )
    vehicle_model = models.ForeignKey(
        VehicleModel, on_delete=models.CASCADE, verbose_name="modelo"
    )
    # Position in the full-text ranking: 0 is the most relevant.
    rank = models.PositiveIntegerField("relevância", default=0)
    status = models.CharField(
        "situação", max_length=12, choices=CollectionStatus.choices,
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
```

- [ ] **Step 2: Gerar e aplicar a migração**

```bash
./venv/bin/python manage.py makemigrations crawler --name collection_queue
./venv/bin/python manage.py migrate
```
Expected: cria `crawler/migrations/0006_collection_queue.py` com `CreateModel` para as duas
tabelas; `migrate` aplica sem erro.

- [ ] **Step 3: Verificar que o M2M usa a through**

```bash
./venv/bin/python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','carprice.settings')
django.setup()
from crawler.models import CollectionRequest
print(CollectionRequest.vehicle_models.through.__name__)
"
```
Expected: `CollectionItem` (e não uma tabela auto-gerada).

- [ ] **Step 4: Commit**

```bash
git add crawler/models.py crawler/migrations/
git commit -m "feat: Tabelas da fila de coleta sob demanda"
```

---

## Task 2: `periods_for`

**Files:**
- Create: `crawler/services/scheduling.py`
- Create: `crawler/tests/test_scheduling.py`

- [ ] **Step 1: Escrever o teste que falha**

`crawler/tests/test_scheduling.py`:

```python
from datetime import date

from django.test import SimpleTestCase

from crawler.models import ZERO_KM_YEAR
from crawler.services import scheduling


class PeriodsForTests(SimpleTestCase):
    today = date(2026, 8, 1)

    def test_recent_points_come_first(self):
        periods = scheduling.periods_for(2015, self.today)
        self.assertEqual(periods[:4], [(2026, 8), (2026, 5), (2026, 2), (2025, 8)])

    def test_yearly_step_stops_one_year_after_the_version(self):
        periods = scheduling.periods_for(2015, self.today)
        self.assertEqual(periods[-1], (2016, 8))
        self.assertEqual(len(periods), 13)

    def test_the_twelve_month_point_and_the_first_yearly_step_collapse(self):
        # Both are (2025, 8); the set must not offer it twice.
        self.assertEqual(scheduling.periods_for(2015, self.today).count((2025, 8)), 1)

    def test_zero_km_has_no_yearly_step(self):
        self.assertEqual(
            scheduling.periods_for(ZERO_KM_YEAR, self.today),
            [(2026, 8), (2026, 5), (2026, 2), (2025, 8)],
        )

    def test_a_current_year_version_has_no_yearly_step(self):
        self.assertEqual(len(scheduling.periods_for(2026, self.today)), 4)

    def test_crossing_the_year_boundary(self):
        # January: three months back lands in October of the previous year.
        self.assertEqual(
            scheduling.periods_for(2024, date(2026, 1, 1)),
            [(2026, 1), (2025, 10), (2025, 7), (2025, 1)],
        )

    def test_an_old_version_gets_the_full_yearly_ladder(self):
        periods = scheduling.periods_for(1996, self.today)
        self.assertEqual(periods[-1], (1997, 8))
        self.assertEqual(len(periods), 32)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test crawler.tests.test_scheduling -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'crawler.services.scheduling'`.

- [ ] **Step 3: Escrever a função**

`crawler/services/scheduling.py`:

```python
"""Scheduling for on-demand collection.

Never touches the FIPE API: it turns a search into rows the worker will later
execute. That is what keeps the web app on the right side of the boundary — it
writes a request, it never runs a collection.
"""

from crawler.models import ZERO_KM_YEAR

# Recent resolution: current month, then three, six and twelve months back.
# Matches the 3/6/12 variation windows the detail screen already shows.
MONTHS_BACK = (0, 3, 6, 12)


def _shift(year, month, months):
    """``(2026, 1)`` shifted back 3 months -> ``(2025, 10)``."""
    total = year * 12 + (month - 1) - months
    return total // 12, total % 12 + 1


def periods_for(version_year, today):
    """The ``(year, month)`` periods to collect for one version, newest first.

    The yearly ladder stops one year after the version's own year: a 2020
    version has no price in the 2005 table, and asking is a guaranteed 404 that
    still costs a slot of the quota. A 0 km vehicle (year 32000) is a
    current-year car, so it gets no ladder at all.
    """
    periods = {_shift(today.year, today.month, months) for months in MONTHS_BACK}
    if version_year != ZERO_KM_YEAR:
        for year in range(version_year + 1, today.year):
            periods.add((year, today.month))
    return sorted(periods, reverse=True)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `./venv/bin/python manage.py test crawler.tests.test_scheduling -v 2`
Expected: PASS, 7 testes. Nenhum banco é criado — é `SimpleTestCase` sobre função pura.

- [ ] **Step 5: Commit**

```bash
git add crawler/services/scheduling.py crawler/tests/test_scheduling.py
git commit -m "feat: Expansão de períodos para a coleta sob demanda"
```

---

## Task 3: Dedup por cobertura e `request_collection`

**Files:**
- Modify: `crawler/services/scheduling.py`
- Modify: `crawler/tests/test_scheduling.py`

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar a `crawler/tests/test_scheduling.py`:

```python
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from crawler.models import Brand
from crawler.models import CollectionRequest
from crawler.models import CollectionStatus
from crawler.models import VehicleModel


def build_models(count):
    brand, _ = Brand.objects.get_or_create(fipe_code=21, defaults={"name": "Fiat"})
    return [
        VehicleModel.objects.create(brand=brand, fipe_code=index, name=f"Modelo {index}")
        for index in range(1, count + 1)
    ]


class RequestCollectionTests(TestCase):
    def setUp(self):
        self.models = build_models(4)
        self.ids = [model.pk for model in self.models]

    def test_creates_a_request_with_one_item_per_model_in_rank_order(self):
        request = scheduling.request_collection("palio", self.ids)

        self.assertEqual(request.term, "palio")
        self.assertEqual(request.status, CollectionStatus.PENDING)
        self.assertEqual(
            list(request.items.values_list("vehicle_model_id", "rank")),
            [(self.ids[0], 0), (self.ids[1], 1), (self.ids[2], 2), (self.ids[3], 3)],
        )

    def test_an_empty_search_schedules_nothing(self):
        self.assertIsNone(scheduling.request_collection("", []))
        self.assertIsNone(scheduling.request_collection("palio", []))
        self.assertEqual(CollectionRequest.objects.count(), 0)

    def test_a_fully_covered_search_reuses_the_existing_request(self):
        first = scheduling.request_collection("palio", self.ids)
        again = scheduling.request_collection("palio fire", self.ids[:2])

        self.assertEqual(again.pk, first.pk)
        self.assertEqual(CollectionRequest.objects.count(), 1)

    def test_a_partly_covered_search_schedules_only_the_rest(self):
        scheduling.request_collection("palio", self.ids[:2])
        second = scheduling.request_collection("fiat", self.ids)

        self.assertEqual(CollectionRequest.objects.count(), 2)
        self.assertEqual(
            list(second.items.values_list("vehicle_model_id", flat=True)), self.ids[2:]
        )

    def test_coverage_expires_after_the_window(self):
        first = scheduling.request_collection("palio", self.ids)
        CollectionRequest.objects.filter(pk=first.pk).update(
            created_at=timezone.now() - timedelta(hours=49)
        )

        second = scheduling.request_collection("palio", self.ids)

        self.assertNotEqual(second.pk, first.pk)
        self.assertEqual(second.items.count(), 4)

    def test_a_pending_request_still_counts_as_covering(self):
        # Otherwise a slow queue would schedule the same models over and over.
        first = scheduling.request_collection("palio", self.ids)
        self.assertEqual(first.status, CollectionStatus.PENDING)

        self.assertEqual(scheduling.request_collection("palio", self.ids).pk, first.pk)

    def test_a_finished_request_still_covers_inside_the_window(self):
        first = scheduling.request_collection("palio", self.ids)
        CollectionRequest.objects.filter(pk=first.pk).update(
            status=CollectionStatus.COMPLETED
        )

        self.assertEqual(scheduling.request_collection("palio", self.ids).pk, first.pk)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test crawler.tests.test_scheduling.RequestCollectionTests -v 2`
Expected: FAIL — `AttributeError: module 'crawler.services.scheduling' has no attribute 'request_collection'`.

- [ ] **Step 3: Implementar**

Acrescentar a `crawler/services/scheduling.py` (e os imports no topo):

```python
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from crawler.models import CollectionItem
from crawler.models import CollectionRequest
from crawler.models import VehicleType

# A model asked for inside this window is not asked for again.
COVERAGE_WINDOW = timedelta(hours=48)


def covered_model_ids(now=None):
    """Models already asked for by a request inside the coverage window.

    Every status counts, pending included: a slow queue must not produce
    duplicate requests for models nobody has collected yet.
    """
    cutoff = (now or timezone.now()) - COVERAGE_WINDOW
    return set(
        CollectionItem.objects.filter(request__created_at__gte=cutoff).values_list(
            "vehicle_model_id", flat=True
        )
    )


def covering_request(model_ids, now=None):
    """The most recent request inside the window touching any of these models."""
    cutoff = (now or timezone.now()) - COVERAGE_WINDOW
    return (
        CollectionRequest.objects.filter(
            created_at__gte=cutoff, items__vehicle_model_id__in=model_ids
        )
        .order_by("-created_at")
        .first()
    )


def request_collection(term, model_ids, vehicle_type=VehicleType.CAR):
    """Schedule the collection of `model_ids`, skipping what is already covered.

    Returns the request that now covers the search — a fresh one, the existing
    one when everything was already asked for, or None when there is nothing to
    collect. Never calls FIPE.
    """
    if not term or not model_ids:
        return None

    covered = covered_model_ids()
    missing = [model_id for model_id in model_ids if model_id not in covered]
    if not missing:
        return covering_request(model_ids)

    with transaction.atomic():
        request = CollectionRequest.objects.create(term=term, vehicle_type=vehicle_type)
        CollectionItem.objects.bulk_create(
            [
                CollectionItem(request=request, vehicle_model_id=model_id, rank=rank)
                # rank comes from the caller's order, which is the FTS ranking.
                for rank, model_id in enumerate(missing)
            ]
        )
    return request
```

- [ ] **Step 4: Rodar e ver passar**

Run: `./venv/bin/python manage.py test crawler.tests.test_scheduling -v 2`
Expected: PASS, 14 testes.

- [ ] **Step 5: Commit**

```bash
git add crawler/services/scheduling.py crawler/tests/test_scheduling.py
git commit -m "feat: Dedup por cobertura de modelos no agendamento"
```

---

## Task 4: `upsert_quote` reutilizável

O `_upsert_quote` atual exige um `CrawlRun` para contar. O worker não tem um. Extrai-se o
miolo, sem duplicar a lógica de upsert.

**Files:**
- Modify: `crawler/services/sync.py:411-437` (a função `_upsert_quote`)
- Modify: `crawler/tests/test_sync.py`

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar a `crawler/tests/test_sync.py`:

```python
class UpsertQuoteTests(TestCase):
    def setUp(self):
        self.client_ = FakeFipeClient()
        self.reference = sync.resolve_reference_table(self.client_)
        self.brand = Brand.objects.create(fipe_code=21, name="Fiat")
        self.model = VehicleModel.objects.create(
            brand=self.brand, fipe_code=4828, name="Uno Mille 1.0"
        )
        self.version = ModelYear.objects.create(
            vehicle_model=self.model, fipe_year_code="2013-1", year=2013, fuel_type=1
        )

    def outcome(self):
        return sync.upsert_quote(
            self.client_, self.reference, VehicleType.CAR, self.brand, self.model, self.version
        )

    def test_reports_created_then_updated(self):
        self.assertEqual(self.outcome(), sync.QuoteOutcome.CREATED)
        self.assertEqual(self.outcome(), sync.QuoteOutcome.UPDATED)
        self.assertEqual(PriceQuote.objects.count(), 1)

    def test_reports_missing_when_fipe_cannot_price_it(self):
        self.client_ = FakeFipeClient(missing={"2013-1"})
        self.assertEqual(self.outcome(), sync.QuoteOutcome.MISSING)
        self.assertEqual(PriceQuote.objects.count(), 0)
```

Importar `VehicleType` no topo do arquivo de teste, junto dos outros models.

- [ ] **Step 2: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test crawler.tests.test_sync.UpsertQuoteTests -v 2`
Expected: FAIL — `AttributeError: module 'crawler.services.sync' has no attribute 'upsert_quote'`.

- [ ] **Step 3: Extrair a função**

Em `crawler/services/sync.py`, substituir `_upsert_quote` por:

```python
class QuoteOutcome(enum.StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    MISSING = "missing"


def upsert_quote(client, reference, vehicle_type, brand, vehicle_model, model_year):
    """Fetch and store one quote. Returns which of the three things happened.

    Counter-free so both callers can use it: the CrawlRun sweep and the
    on-demand worker, which has no run to count into.
    """
    try:
        payload = client.price(
            reference.fipe_code,
            vehicle_type,
            brand.fipe_code,
            vehicle_model.fipe_code,
            model_year.fipe_year_code,
        )
    except FipeNotFound:
        # FIPE lists year/fuel combinations it cannot price. Skipping is correct.
        logger.info("sem cotação para %s", model_year.fipe_year_code)
        return QuoteOutcome.MISSING

    quote_data = parsers.parse_quote(payload)
    _, created = PriceQuote.objects.update_or_create(
        model_year=model_year,
        reference_table=reference,
        defaults={
            "value": quote_data.value,
            "fipe_code": quote_data.fipe_code,
            "fuel_type": quote_data.fuel_type,
        },
    )
    return QuoteOutcome.CREATED if created else QuoteOutcome.UPDATED


def _upsert_quote(client, run, reference, vehicle_type, brand, vehicle_model, model_year):
    outcome = upsert_quote(
        client, reference, vehicle_type, brand, vehicle_model, model_year
    )
    if outcome is QuoteOutcome.CREATED:
        run.quotes_created += 1
    elif outcome is QuoteOutcome.UPDATED:
        run.quotes_updated += 1
```

Acrescentar `import enum` no topo do módulo.

- [ ] **Step 4: Rodar e ver passar**

Run: `./venv/bin/python manage.py test crawler -v 2`
Expected: PASS. Os testes existentes de `sync` continuam verdes — o comportamento do caminho
com `CrawlRun` não mudou.

- [ ] **Step 5: Commit**

```bash
git add crawler/services/sync.py crawler/tests/test_sync.py
git commit -m "refactor: upsert_quote reutilizável, sem depender de CrawlRun"
```

---

## Task 5: Mapa de tabelas de referência

**Files:**
- Modify: `crawler/services/sync.py`
- Modify: `crawler/tests/test_sync.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
class ReferenceTableMapTests(TestCase):
    def test_maps_every_period_fipe_offers(self):
        client = FakeFipeClient()
        table = sync.reference_table_map(client)

        self.assertEqual(set(table), {(2025, 6), (2025, 5), (2025, 4)})
        self.assertEqual(table[(2025, 6)].fipe_code, 322)

    def test_creates_the_rows_once(self):
        sync.reference_table_map(FakeFipeClient())
        sync.reference_table_map(FakeFipeClient())

        self.assertEqual(ReferenceTable.objects.count(), 3)

    def test_costs_a_single_request(self):
        client = FakeFipeClient()
        sync.reference_table_map(client)

        self.assertEqual(client.count("reference_tables"), 1)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test crawler.tests.test_sync.ReferenceTableMapTests -v 2`
Expected: FAIL — `AttributeError: module 'crawler.services.sync' has no attribute 'reference_table_map'`.

- [ ] **Step 3: Implementar**

Acrescentar a `crawler/services/sync.py`, logo depois de `resolve_reference_table`:

```python
def reference_table_map(client):
    """``{(year, month): ReferenceTable}`` for every table FIPE offers.

    One request, resolved once per worker pass: the on-demand queue asks for
    dozens of periods and must not re-fetch the catalogue for each one. A period
    absent from this map is one FIPE does not have, and is skipped rather than
    treated as an error.
    """
    table = {}
    for data in parsers.parse_reference_tables(client.reference_tables()):
        reference, _ = ReferenceTable.objects.get_or_create(
            fipe_code=data.fipe_code, defaults={"month": data.month, "year": data.year}
        )
        table[(data.year, data.month)] = reference
    return table
```

- [ ] **Step 4: Rodar e ver passar**

Run: `./venv/bin/python manage.py test crawler -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crawler/services/sync.py crawler/tests/test_sync.py
git commit -m "feat: Mapa de tabelas de referência resolvido numa requisição"
```

---

## Task 6: O motor do worker

**Files:**
- Create: `crawler/services/collecting.py`
- Create: `crawler/tests/test_collecting.py`

- [ ] **Step 1: Escrever o teste que falha**

`crawler/tests/test_collecting.py`:

```python
from django.test import TestCase

from crawler.models import Brand
from crawler.models import CollectionRequest
from crawler.models import CollectionStatus
from crawler.models import ModelYear
from crawler.models import PriceQuote
from crawler.models import ReferenceTable
from crawler.models import VehicleModel
from crawler.services import collecting
from crawler.services import scheduling
from crawler.tests.fake_client import FakeFipeClient


def build_model(fipe_code=4828, years=(2024,)):
    brand, _ = Brand.objects.get_or_create(fipe_code=21, defaults={"name": "Fiat"})
    model = VehicleModel.objects.create(
        brand=brand, fipe_code=fipe_code, name=f"Modelo {fipe_code}"
    )
    for year in years:
        ModelYear.objects.create(
            vehicle_model=model, fipe_year_code=f"{year}-1", year=year, fuel_type=1
        )
    return model


class ProcessRequestTests(TestCase):
    """The fixture offers three reference tables: 06, 05 and 04 of 2025."""

    def setUp(self):
        self.client_ = FakeFipeClient()

    def schedule(self, models):
        return scheduling.request_collection("teste", [m.pk for m in models])

    def test_collects_and_marks_the_request_completed(self):
        request = self.schedule([build_model(years=(2024,))])

        collecting.process_request(self.client_, request, budget=100)

        request.refresh_from_db()
        self.assertEqual(request.status, CollectionStatus.COMPLETED)
        self.assertEqual(request.models_done, 1)
        self.assertGreater(request.quotes_created, 0)
        self.assertIsNotNone(request.finished_at)

    def test_skips_pairs_already_in_the_database(self):
        request = self.schedule([build_model(years=(2024,))])
        collecting.process_request(self.client_, request, budget=100)
        collected = PriceQuote.objects.count()

        # Re-open the same request and run it again: every pair is already
        # stored, so it must cost zero calls to the price endpoint.
        request.items.update(status=CollectionStatus.PENDING)
        fresh = FakeFipeClient()
        collecting.process_request(fresh, request, budget=100)

        self.assertEqual(PriceQuote.objects.count(), collected)
        self.assertEqual(fresh.count("price"), 0)

    def test_budget_leaves_the_request_partial_and_the_item_pending(self):
        request = self.schedule([build_model(years=(2020, 2021, 2022))])

        collecting.process_request(self.client_, request, budget=2)

        request.refresh_from_db()
        self.assertEqual(request.status, CollectionStatus.PARTIAL)
        self.assertEqual(request.requests_spent, 2)
        self.assertEqual(request.items.filter(status=CollectionStatus.PENDING).count(), 1)

    def test_a_second_pass_resumes_where_it_stopped(self):
        request = self.schedule([build_model(years=(2020, 2021, 2022))])
        collecting.process_request(self.client_, request, budget=2)
        partial = PriceQuote.objects.count()

        collecting.process_request(FakeFipeClient(), request, budget=100)

        request.refresh_from_db()
        self.assertEqual(request.status, CollectionStatus.COMPLETED)
        self.assertGreater(PriceQuote.objects.count(), partial)

    def test_the_newest_month_of_every_version_comes_first(self):
        # Period-major order: an exhausted budget must leave a complete snapshot
        # of the current month, not the full history of one version.
        request = self.schedule([build_model(years=(2020, 2021, 2022))])

        collecting.process_request(self.client_, request, budget=3)

        newest = ReferenceTable.objects.order_by("-year", "-month").first()
        self.assertEqual(PriceQuote.objects.filter(reference_table=newest).count(), 3)

    def test_a_period_fipe_does_not_have_is_skipped(self):
        # The fixture stops at 04/2025, so older yearly steps simply do not exist.
        request = self.schedule([build_model(years=(2015,))])

        collecting.process_request(self.client_, request, budget=100)

        request.refresh_from_db()
        self.assertEqual(request.status, CollectionStatus.COMPLETED)

    def test_a_combination_fipe_cannot_price_is_counted_as_missing(self):
        request = self.schedule([build_model(years=(2024,))])

        collecting.process_request(FakeFipeClient(missing={"2024-1"}), request, budget=100)

        request.refresh_from_db()
        self.assertGreater(request.quotes_missing, 0)
        self.assertEqual(request.quotes_created, 0)
        self.assertEqual(request.status, CollectionStatus.COMPLETED)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test crawler.tests.test_collecting -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'crawler.services.collecting'`.

- [ ] **Step 3: Escrever o motor**

`crawler/services/collecting.py`:

```python
"""The on-demand collection worker's engine.

Runs one CollectionRequest at a time, spending a bounded number of FIPE
requests. Everything here assumes a single process: the quota is a sliding
window inside one FipeClient, and a second worker would silently double it.
"""

import logging

from django.utils import timezone

from crawler.models import CollectionItem
from crawler.models import CollectionRequest
from crawler.models import CollectionStatus
from crawler.models import PriceQuote
from crawler.services import scheduling
from crawler.services import sync

logger = logging.getLogger(__name__)

DEFAULT_BUDGET = 1500


def pending_requests():
    """Requests with work left, oldest first."""
    return (
        CollectionRequest.objects.filter(items__status=CollectionStatus.PENDING)
        .distinct()
        .order_by("created_at")
    )


def reclaim_stale_requests():
    """Return to PARTIAL anything a dead worker left RUNNING.

    Without this the request would keep a status nobody ever moves again, and
    its pending items would never be picked up.
    """
    return CollectionRequest.objects.filter(status=CollectionStatus.RUNNING).update(
        status=CollectionStatus.PARTIAL
    )


def _work_plan(vehicle_model, today):
    """``(recency, period, version)`` for one model, newest period first.

    Sorted by how recent the period is *across versions*, not version by
    version: with the budget exhausted, a complete snapshot of the current month
    is worth more than the full history of two versions and nothing of the rest.
    """
    plan = []
    for version in vehicle_model.model_years.all():
        for recency, period in enumerate(scheduling.periods_for(version.year, today)):
            plan.append((recency, period, version))
    plan.sort(key=lambda row: (row[0], row[2].pk))
    return plan


def _collect_model(client, request, item, references, budget, today):
    """Spend at most `budget` requests on one model. Returns what it spent."""
    vehicle_model = item.vehicle_model
    brand = vehicle_model.brand
    spent = 0

    for _, period, version in _work_plan(vehicle_model, today):
        if spent >= budget:
            return spent
        reference = references.get(period)
        if reference is None:
            continue  # FIPE has no table for that month; not an error.
        if PriceQuote.objects.filter(
            model_year=version, reference_table=reference
        ).exists():
            continue

        outcome = sync.upsert_quote(
            client, reference, request.vehicle_type, brand, vehicle_model, version
        )
        spent += 1
        if outcome is sync.QuoteOutcome.CREATED:
            request.quotes_created += 1
        elif outcome is sync.QuoteOutcome.UPDATED:
            request.quotes_updated += 1
        else:
            request.quotes_missing += 1
    return spent


def process_request(client, request, budget=DEFAULT_BUDGET, today=None, progress=None):
    """Work one request until it finishes or the budget runs out.

    Leaves the request COMPLETED or PARTIAL. An item interrupted mid-way stays
    PENDING and is redone on the next pass — cheaply, because pairs already
    stored are skipped.
    """
    report = progress or (lambda message: None)
    today = today or timezone.localdate()

    request.status = CollectionStatus.RUNNING
    if request.started_at is None:
        request.started_at = timezone.now()
    request.save(update_fields=["status", "started_at"])

    references = sync.reference_table_map(client)
    spent = 0

    for item in request.items.filter(status=CollectionStatus.PENDING):
        if spent >= budget:
            break
        spent += _collect_model(
            client, request, item, references, budget - spent, today
        )
        if spent >= budget and _model_has_work_left(item, references, today):
            break  # item stays PENDING; the next pass resumes it
        item.status = CollectionStatus.COMPLETED
        item.finished_at = timezone.now()
        item.save(update_fields=["status", "finished_at"])
        request.models_done += 1
        report(f"  {item.vehicle_model}: concluído ({spent} requisições na passada)")

    request.requests_spent += spent
    still_pending = request.items.filter(status=CollectionStatus.PENDING).exists()
    request.status = (
        CollectionStatus.PARTIAL if still_pending else CollectionStatus.COMPLETED
    )
    if not still_pending:
        request.finished_at = timezone.now()
    request.save()
    return spent


def _model_has_work_left(item, references, today):
    """Is there any uncollected pair left for this model?"""
    for _, period, version in _work_plan(item.vehicle_model, today):
        reference = references.get(period)
        if reference is None:
            continue
        if not PriceQuote.objects.filter(
            model_year=version, reference_table=reference
        ).exists():
            return True
    return False
```

- [ ] **Step 4: Rodar e ver passar**

Run: `./venv/bin/python manage.py test crawler.tests.test_collecting -v 2`
Expected: PASS, 7 testes.

- [ ] **Step 5: Commit**

```bash
git add crawler/services/collecting.py crawler/tests/test_collecting.py
git commit -m "feat: Motor da coleta sob demanda, com orçamento e ordem período-major"
```

---

## Task 7: Lock e o comando `process_crawl_queue`

**Files:**
- Create: `crawler/management/commands/process_crawl_queue.py`
- Modify: `crawler/tests/test_collecting.py`
- Modify: `.gitignore`

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar a `crawler/tests/test_collecting.py`:

```python
import tempfile
from pathlib import Path

from crawler.services.collecting import queue_lock


class LockTests(TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "queue.lock"

    def test_a_second_holder_is_refused(self):
        with queue_lock(self.path):
            with self.assertRaises(collecting.QueueBusy):
                with queue_lock(self.path):
                    pass

    def test_the_lock_is_released_on_exit(self):
        with queue_lock(self.path):
            pass
        with queue_lock(self.path):
            pass  # must not raise


class ReclaimTests(TestCase):
    def test_a_request_left_running_is_reclaimed(self):
        request = scheduling.request_collection("teste", [build_model().pk])
        CollectionRequest.objects.filter(pk=request.pk).update(
            status=CollectionStatus.RUNNING
        )

        collecting.reclaim_stale_requests()

        request.refresh_from_db()
        self.assertEqual(request.status, CollectionStatus.PARTIAL)
        self.assertIn(request, collecting.pending_requests())
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test crawler.tests.test_collecting.LockTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'queue_lock'`.

- [ ] **Step 3: Implementar o lock**

Acrescentar a `crawler/services/collecting.py` (e `import contextlib`, `import fcntl` no topo):

```python
class QueueBusy(Exception):
    """Another worker holds the queue lock."""


@contextlib.contextmanager
def queue_lock(path):
    """Hold an exclusive, non-blocking lock on `path`.

    A file lock rather than a database row because the OS releases it when the
    process dies, which is exactly the failure this must survive. It guards a
    single machine, not a cluster — the quota lives in one process's memory
    anyway.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise QueueBusy(f"outro worker já detém {path}") from exc
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
```

- [ ] **Step 4: Escrever o comando**

`crawler/management/commands/process_crawl_queue.py`:

```python
"""Consumes the on-demand collection queue. Argument parsing and reporting only —
the work lives in ``crawler.services.collecting``.
"""

import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from crawler.fipe import FipeClient, FipeError
from crawler.fipe.client import DEFAULT_REQUESTS_PER_MINUTE
from crawler.services import collecting

LOCK_PATH = Path(settings.BASE_DIR) / ".crawl_queue.lock"


class Command(BaseCommand):
    help = "Executa as coletas agendadas pelas buscas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--forever",
            action="store_true",
            help="Fica em laço, dormindo --interval entre as passadas.",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=60.0,
            help="Segundos entre passadas com --forever. Padrão: 60.",
        )
        parser.add_argument(
            "--budget",
            type=int,
            default=collecting.DEFAULT_BUDGET,
            help=(
                "Máximo de requisições gastas em cada pedido por passada; esgotado, "
                f"o worker passa ao próximo. Padrão: {collecting.DEFAULT_BUDGET}."
            ),
        )
        parser.add_argument(
            "--requests-per-minute",
            type=int,
            default=DEFAULT_REQUESTS_PER_MINUTE,
            help=f"Cota por minuto corrido. Padrão: {DEFAULT_REQUESTS_PER_MINUTE}.",
        )

    def handle(self, *args, **options):
        client = FipeClient(
            requests_per_minute=options["requests_per_minute"],
            on_wait=lambda message: self.log(f"  {message}", self.style.WARNING),
        )
        try:
            with collecting.queue_lock(LOCK_PATH):
                self._run(client, options)
        except collecting.QueueBusy as exc:
            raise CommandError(f"{exc}. Só um worker por vez — a cota depende disso.")

    def _run(self, client, options):
        while True:
            reclaimed = collecting.reclaim_stale_requests()
            if reclaimed:
                self.log(f"{reclaimed} pedido(s) retomado(s) de um worker anterior.")
            self._pass(client, options["budget"])
            if not options["forever"]:
                return
            self._sleep(options["interval"])

    def _pass(self, client, budget):
        requests = list(collecting.pending_requests())
        if not requests:
            self.log("Nada na fila.")
            return
        for request in requests:
            self.log(f"{request.term}: {request.items.count()} modelos")
            try:
                spent = collecting.process_request(
                    client, request, budget=budget, progress=self.log
                )
            except FipeError as exc:
                request.last_error = f"{type(exc).__name__}: {exc}"
                request.status = collecting.CollectionStatus.PARTIAL
                request.save(update_fields=["last_error", "status"])
                raise CommandError(str(exc)) from exc
            self.log(
                self.style.SUCCESS(
                    f"  {request.get_status_display()}: {spent} requisições, "
                    f"{request.quotes_created} cotações novas, "
                    f"{request.quotes_updated} atualizadas, "
                    f"{request.quotes_missing} sem preço na FIPE."
                )
            )

    def _sleep(self, seconds):
        time.sleep(seconds)

    def log(self, message, style=None):
        """Every log line carries the moment it was written."""
        stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        text = f"[{stamp}] {message}"
        self.stdout.write(style(text) if style else text)
```

- [ ] **Step 5: Ignorar o lock no git**

Acrescentar ao `.gitignore`:

```
# Lock do worker da fila de coleta.
/.crawl_queue.lock
```

- [ ] **Step 6: Rodar e ver passar**

```bash
./venv/bin/python manage.py test crawler
./venv/bin/python manage.py process_crawl_queue
```
Expected: testes verdes; o comando imprime `Nada na fila.` e sai (ainda não há agendamentos).

- [ ] **Step 7: Commit**

```bash
git add crawler/services/collecting.py crawler/management/commands/process_crawl_queue.py \
        crawler/tests/test_collecting.py .gitignore
git commit -m "feat: Comando process_crawl_queue com lock de worker único"
```

---

## Task 8: Disparo pela busca e faixa de estado

**Files:**
- Modify: `web/views.py`
- Create: `web/templates/web/partials/collection_status.html`
- Modify: `web/templates/web/home.html`
- Modify: `web/tests/test_views.py`

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar a `web/tests/test_views.py`:

```python
class CollectionSchedulingTests(TestCase):
    def setUp(self):
        self.uno = build_vehicle(model_code=1, year=2015, fuel=FuelType.FLEX)
        self.uno.vehicle_model.name = "Uno Mille Fire"
        self.uno.vehicle_model.save()
        add_quote(self.uno, 2026, 8, "40000.00")

    def test_a_search_with_a_term_schedules_a_collection(self):
        self.client.get(reverse("web:home"), {"q": "mille"})

        request = CollectionRequest.objects.get()
        self.assertEqual(request.term, "mille")
        self.assertEqual(
            list(request.items.values_list("vehicle_model_id", flat=True)),
            [self.uno.vehicle_model.pk],
        )

    def test_a_search_without_a_term_schedules_nothing(self):
        self.client.get(reverse("web:home"))
        self.client.get(reverse("web:home"), {"fuel": "5"})

        self.assertEqual(CollectionRequest.objects.count(), 0)

    def test_a_repeated_search_does_not_schedule_again(self):
        self.client.get(reverse("web:home"), {"q": "mille"})
        self.client.get(reverse("web:home"), {"q": "mille"})

        self.assertEqual(CollectionRequest.objects.count(), 1)

    def test_the_banner_says_scheduled_not_collecting(self):
        # The web app cannot know whether the worker is running; claiming it is
        # would be a lie every time it is not.
        response = self.client.get(reverse("web:home"), {"q": "mille"})

        self.assertContains(response, "agendada")
        self.assertNotContains(response, "Coletando agora")

    def test_no_banner_without_a_collection(self):
        response = self.client.get(reverse("web:home"))
        self.assertNotContains(response, "histórico")
```

Importar `CollectionRequest` de `crawler.models` no topo do arquivo.

- [ ] **Step 2: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test web.tests.test_views.CollectionSchedulingTests -v 2`
Expected: FAIL — `CollectionRequest.DoesNotExist`, porque a view ainda não agenda nada.

- [ ] **Step 3: Disparar o agendamento na view**

Em `web/views.py`, acrescentar o import e trocar `home`:

```python
from crawler.services import scheduling
```

```python
def home(request):
    filters = SearchFilters.from_query(request.GET)
    context = _search_context(filters)
    # Only a term schedules work: tweaking the fuel or year filter must not
    # queue thousands of FIPE requests. The whole match is scheduled, not just
    # the visible page.
    context["collection"] = scheduling.request_collection(
        filters.term, search.search(filters.term) or []
    )
    if request.headers.get("HX-Request"):
        return render(request, "web/partials/results.html", context)
    return render(request, "web/home.html", context)
```

E `from web import search` no topo, se ainda não estiver lá.

- [ ] **Step 4: Escrever a faixa**

`web/templates/web/partials/collection_status.html`:

```html
{% if collection %}
  <p class="mt-4 rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900">
    {% if collection.status == "completed" %}
      Histórico destes modelos coletado em {{ collection.finished_at|date:"d/m H:i" }}.
    {% elif collection.status == "partial" %}
      Coletando histórico — {{ collection.models_done }} de {{ collection.models_total }} modelos.
    {% else %}
      Coleta de histórico <strong>agendada</strong> para {{ collection.models_total }} modelo{{ collection.models_total|pluralize }}.
      Roda quando o worker estiver ativo.
    {% endif %}
  </p>
{% endif %}
```

- [ ] **Step 5: Incluir a faixa na busca**

Em `web/templates/web/home.html`, logo depois do `<div class="flex gap-2">…</div>` que fecha a
barra de busca e antes do `<div class="mt-6 grid …">`:

```html
    {% include "web/partials/collection_status.html" %}
```

- [ ] **Step 6: Rodar e ver passar**

Run: `./venv/bin/python manage.py test web -v 2`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add web/views.py web/templates/web/ web/tests/test_views.py
git commit -m "feat: A busca agenda a coleta do histórico dos modelos encontrados"
```

---

## Task 9: Teste de fronteira e documentação

**Files:**
- Create: `web/tests/test_boundary.py`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Escrever o teste de fronteira**

`web/tests/test_boundary.py`:

```python
"""The rule that keeps the apps apart: web writes a request, never runs a crawl."""

import ast
from pathlib import Path

from django.test import SimpleTestCase

WEB = Path(__file__).resolve().parent.parent
FORBIDDEN = {"FipeClient", "crawler.fipe"}


def imported_names(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            yield module
            for alias in node.names:
                yield f"{module}.{alias.name}"
                yield alias.name


class BoundaryTests(SimpleTestCase):
    def test_web_never_imports_the_fipe_client(self):
        offenders = []
        for path in WEB.rglob("*.py"):
            if "tests" in path.parts:
                continue
            for name in imported_names(path):
                if any(name.startswith(bad) or name == bad for bad in FORBIDDEN):
                    offenders.append(f"{path.relative_to(WEB.parent)}: {name}")

        self.assertEqual(
            offenders,
            [],
            "web deve agendar coleta, nunca executá-la — use crawler.services.scheduling",
        )
```

- [ ] **Step 2: Rodar e ver passar**

Run: `./venv/bin/python manage.py test web.tests.test_boundary -v 2`
Expected: PASS. Para conferir que o teste realmente pega algo, acrescente
`from crawler.fipe import FipeClient` no topo de `web/views.py`, rode de novo (deve FALHAR
nomeando o arquivo) e desfaça.

- [ ] **Step 3: Documentar no CLAUDE.md**

Acrescentar uma seção `## Coleta sob demanda` depois de `## Coleta`:

````markdown
## Coleta sob demanda

Buscar por um veículo agenda a coleta do histórico daqueles modelos. A busca só grava o pedido;
quem fala com a FIPE é um worker separado:

```bash
python manage.py process_crawl_queue              # uma passada
python manage.py process_crawl_queue --forever    # laço, com --interval (padrão 60s)
python manage.py process_crawl_queue --budget 300 # menos por pedido, mais rodízio
```

**Nada coleta sozinho até o worker estar rodando.** É o preço de não usar thread na view, e o
motivo é a cota: a janela deslizante vive na memória de um `FipeClient`, então um processo
consumidor é a única forma de honrar 40 req/min. Dois workers web dariam 80 e trariam o 429 de
volta. Um lock de arquivo (`.crawl_queue.lock`, via `fcntl.flock`) garante o processo único —
por máquina, não por cluster.

Períodos coletados por versão: mês atual, 3, 6 e 12 meses atrás, mais o mesmo mês a cada ano até
`ano_da_versão + 1`. O piso é **por versão, não por modelo**: uma versão 2020 não tem preço na
tabela de 2005, e perguntar é 404 garantido gastando um slot. Veículo 0 km (ano 32000) não tem
passo anual.

Dedup é por **cobertura de modelos**, não por semelhança de texto: um modelo pedido nas últimas
48h não é pedido de novo, em qualquer status. Assim `palio fire` depois de `palio` não reagenda
nada, e uma busca mais ampla agenda só a parte nova. Par `(versão, mês)` que já tem cotação é
pulado, o que torna a repetição barata.

O orçamento (`--budget`) é **por pedido dentro de uma passada**: esgotado, o worker passa ao
próximo pedido e retoma este na passada seguinte. Sem isso, uma busca por "gol" (16 mil
requisições, ~7h) monopolizaria a fila. Dentro de cada modelo a varredura é **por período, não
por versão** — o mês atual de todas as versões primeiro — para que um orçamento estourado deixe
um retrato completo do mês corrente.

Fronteira, agora com teste (`web/tests/test_boundary.py`): **`web` escreve pedido, nunca executa
coleta.**
````

- [ ] **Step 4: Verificação final**

```bash
./venv/bin/python manage.py test
./venv/bin/python manage.py check
```
Expected: tudo verde, nenhum aviso.

- [ ] **Step 5: Commit**

```bash
git add web/tests/test_boundary.py CLAUDE.md
git commit -m "docs: Documenta a coleta sob demanda e trava a fronteira com teste"
```

---

## Riscos conhecidos

- **A fixture de teste só tem três tabelas de referência** (06, 05 e 04 de 2025), então os
  testes do worker exercitam poucos períodos por versão. É suficiente para provar ordem,
  orçamento e pulo de par existente; a aritmética de períodos é coberta à parte, sem banco.
- **`fcntl` é POSIX.** O lock não funciona em Windows. O projeto já assume Linux (o shell do
  usuário é fish, o binário do Tailwind é `linux-x64`), mas está registrado.
- **A cota é global e o worker é único**, então uma coleta sob demanda longa atrasa qualquer
  `crawl_fipe` rodando em paralelo — na verdade os dois competem pela mesma API sem se
  conhecerem. Rodar os dois ao mesmo tempo dobra a taxa real e provoca 429; o CLAUDE.md diz para
  não fazer isso.
- **O `app.css` precisa ser reconstruído** por causa das classes novas da faixa (`bg-sky-50`,
  `border-sky-200`, `text-sky-900`). É comando do Jorge, não meu.
