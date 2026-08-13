# Busca full-text com filtros — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trocar os selects em cascata por uma barra de pesquisa full-text com filtros de combustível e ano, mostrando os modelos encontrados em cards.

**Architecture:** A busca usa FTS5 do SQLite numa tabela virtual sincronizada por triggers SQL, isolada atrás de `web/search.py` — nenhuma view vê `MATCH` ou `bm25`. Os filtros agem em `ModelYear`, mas o card é um `VehicleModel`, então há uma etapa de agregação (`Min`/`Max`/`Count`) sobre as cotações do mês vigente. `web/filters.py` traduz querystring ↔ dataclass e não importa models, então testa sem banco.

**Tech Stack:** Django 6.1, Python 3.14, SQLite com FTS5, HTMX, Tailwind (binário standalone). Zero dependências além do Django.

Design: [`../specs/2026-08-13-busca-full-text-design.md`](../specs/2026-08-13-busca-full-text-design.md)

---

## Estrutura de arquivos

| arquivo | responsabilidade |
|---|---|
| `crawler/models.py` (mod) | `FuelType` ganha 6=Híbrido e 7=Tetrafuel |
| `crawler/fipe/parsers.py` (mod) | `FUEL_BY_LABEL` ganha os dois rótulos |
| `web/migrations/0001_vehicle_model_fts.py` (novo) | tabela FTS5, 4 triggers, backfill |
| `web/search.py` (novo) | **único SQL cru**: termo → ids ranqueados |
| `web/filters.py` (novo) | querystring ↔ `SearchFilters`; sem banco |
| `web/queries.py` (mod) | agregação por modelo; some `brands()`/`vehicle_models()` |
| `web/codes.py` (mod) | ganha código de modelo (3 partes) |
| `web/views.py` (mod) | `home` vira busca; nova `model_detail`; somem os fragmentos |
| `web/templates/web/home.html` (reescrito) | barra + `<aside>` de filtros + `#results` |
| `web/templates/web/partials/results.html` (novo) | grade de cards + paginação (alvo do HTMX) |
| `web/templates/web/partials/model_card.html` (novo) | um card |
| `web/templates/web/model.html` (novo) | versões de um modelo |
| `web/tests/` (novo pacote) | substitui `web/tests.py` |

**Verificado numa cópia do banco antes deste plano:** o SQL da tabela e dos quatro triggers roda; `"citroen"*` acha "Citroën"; `bm25` com pesos ordena; e nenhuma das entradas hostis (`"`, `AND`, `*`, `-corsa`, `((`, `siena"* OR "`) estoura o `MATCH`.

---

## Task 1: Nomear os combustíveis 6 e 7

**Files:**
- Modify: `crawler/models.py:21-25`
- Modify: `crawler/fipe/parsers.py:26-33`
- Test: `crawler/tests/test_parsers.py:70-84`

- [ ] **Step 1: Escrever o teste que falha**

Em `crawler/tests/test_parsers.py`, acrescentar os dois casos ao dicionário de
`ParseFuelTypeTests.test_maps_every_known_label`:

```python
    def test_maps_every_known_label(self):
        cases = {
            "1992 Gasolina": 1,
            "Álcool": 2,
            "2010 Diesel": 3,
            "2022 Elétrico": 4,
            "2026 Flex": 5,
            "2026 Híbrido": 6,
            "2016 Tetrafuel": 7,
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(parsers.parse_fuel_type(label), expected)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test crawler.tests.test_parsers.ParseFuelTypeTests -v 2`
Expected: FAIL — `6 != 1` (hoje "Híbrido" cai no default 1).

- [ ] **Step 3: Acrescentar os códigos ao enum**

Em `crawler/models.py`, dentro de `FuelType`, depois de `FLEX`:

```python
    FLEX = 5, "Flex"
    HYBRID = 6, "Híbrido"
    TETRAFUEL = 7, "Tetrafuel"
```

- [ ] **Step 4: Acrescentar os rótulos ao parser**

Substituir `FUEL_BY_LABEL` em `crawler/fipe/parsers.py`:

```python
FUEL_BY_LABEL = {
    # Mais específico primeiro: parse_fuel_type devolve o primeiro rótulo contido
    # no texto, e a FIPE escreve coisas como "Gasolina/Híbrido".
    "tetrafuel": 7,
    "hibrido": 6,
    "gasolina": 1,
    "alcool": 2,
    "etanol": 2,
    "diesel": 3,
    "eletrico": 4,
    "flex": 5,
}
```

- [ ] **Step 5: Rodar e ver passar**

Run: `./venv/bin/python manage.py test crawler -v 2`
Expected: PASS, 76 testes. `test_unknown_label_falls_back_to_the_default` continua verde —
"Hidrogênio" normaliza para `hidrogenio`, que não contém `hibrido`.

- [ ] **Step 6: Gerar a migração**

Run: `./venv/bin/python manage.py makemigrations crawler`
Expected: cria `crawler/migrations/0005_alter_modelyear_fuel_type_alter_pricequote_fuel_type.py`,
só com `AlterField` de `choices` nos dois campos. **Anote o nome do arquivo** — a Task 2 depende dele.

- [ ] **Step 7: Aplicar e conferir**

```bash
./venv/bin/python manage.py migrate
./venv/bin/python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','carprice.settings')
django.setup()
from crawler.models import ModelYear
print(ModelYear.objects.filter(fuel_type=6).first().get_fuel_type_display())
"
```
Expected: `Híbrido` (antes imprimia o número cru).

- [ ] **Step 8: Commit**

```bash
git add crawler/models.py crawler/fipe/parsers.py crawler/migrations/ crawler/tests/test_parsers.py
git commit -m "feat: Nomeia os combustíveis 6 (Híbrido) e 7 (Tetrafuel)"
```

---

## Task 2: Índice FTS5 e triggers

**Files:**
- Create: `web/migrations/0001_vehicle_model_fts.py`

- [ ] **Step 1: Escrever a migração**

Trocar `0005_alter_modelyear_fuel_type_alter_pricequote_fuel_type` pelo nome real anotado na Task 1.

**`RunSQL` recebe uma lista, não uma string**: o driver `sqlite3` executa um comando por
`execute()`, então um bloco com vários `CREATE` silenciosamente rodaria só o primeiro.

```python
"""The FTS5 index over vehicle model names.

The table is standalone and not `content='crawler_vehiclemodel'` because `brand`
is a foreign key there, not a column: with external content FTS5 would issue
`SELECT name, brand FROM crawler_vehiclemodel` and fail.

Triggers rather than Django signals because they hold for every write — a future
`bulk_create` fires no signal but does fire a trigger.
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
```

- [ ] **Step 2: Aplicar e conferir o backfill**

```bash
./venv/bin/python manage.py migrate web
./venv/bin/python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','carprice.settings')
django.setup()
from django.db import connection
from crawler.models import VehicleModel
with connection.cursor() as c:
    c.execute('SELECT count(*) FROM vehicle_model_fts')
    print('indexadas:', c.fetchone()[0], '| modelos:', VehicleModel.objects.count())
"
```
Expected: os dois números iguais (262 hoje).

- [ ] **Step 3: Provar que o `reverse_sql` funciona**

```bash
./venv/bin/python manage.py migrate web zero
./venv/bin/python manage.py migrate web
```
Expected: ida e volta sem erro. Se o `DROP TABLE` vier antes dos triggers, o SQLite reclama —
por isso a ordem inversa.

- [ ] **Step 4: Commit**

```bash
git add web/migrations/0001_vehicle_model_fts.py
git commit -m "feat: Índice FTS5 de modelos, sincronizado por triggers"
```

---

## Task 3: `web/search.py`

**Files:**
- Create: `web/search.py`
- Create: `web/tests/__init__.py`, `web/tests/test_search.py`
- Move: `web/tests.py` → `web/tests/test_views.py`

- [ ] **Step 1: Transformar os testes em pacote**

```bash
mkdir -p web/tests
git mv web/tests.py web/tests/test_views.py
touch web/tests/__init__.py
./venv/bin/python manage.py test web
```
Expected: 20 testes, PASS. O `manage.py test` descobre `web/tests/test*.py` sem configuração.

- [ ] **Step 2: Escrever o teste que falha**

`web/tests/test_search.py`:

```python
from django.test import TestCase

from crawler.models import Brand
from crawler.models import VehicleModel

from web import search


class BuildMatchQueryTests(TestCase):
    def test_quotes_each_token_with_a_prefix_star(self):
        self.assertEqual(search.build_match_query("corsa sedan"), '"corsa"* AND "sedan"*')

    def test_drops_single_char_tokens_when_a_longer_one_exists(self):
        # "gol 1.0" tokeniza como gol/1/0; os dígitos sozinhos só trazem ruído.
        self.assertEqual(search.build_match_query("gol 1.0"), '"gol"*')

    def test_keeps_a_single_char_term_when_it_is_all_there_is(self):
        self.assertEqual(search.build_match_query("c"), '"c"*')

    def test_input_without_any_word_produces_no_query(self):
        for term in ["", "   ", '"', "*", "((", "...", None]:
            with self.subTest(term=term):
                self.assertEqual(search.build_match_query(term), "")


class SearchTests(TestCase):
    def setUp(self):
        citroen = Brand.objects.create(fipe_code=13, name="Citroën")
        fiat = Brand.objects.create(fipe_code=21, name="Fiat")
        self.aircross = VehicleModel.objects.create(
            brand=citroen, fipe_code=1, name="AIRCROSS Exclusive 1.6 Flex 16V 5p Aut."
        )
        self.siena = VehicleModel.objects.create(
            brand=fiat, fipe_code=2, name="Grand Siena TETRAFUEL 1.4 Evo F. Flex 8V"
        )

    def test_ignores_accents_in_both_directions(self):
        self.assertEqual(search.search("citroen"), [self.aircross.pk])

    def test_matches_by_prefix(self):
        self.assertEqual(search.search("aircro"), [self.aircross.pk])

    def test_multiple_tokens_are_combined_with_and(self):
        self.assertEqual(search.search("grand tetrafuel"), [self.siena.pk])
        self.assertEqual(search.search("grand aircross"), [])

    def test_no_term_is_not_the_same_as_no_result(self):
        self.assertIsNone(search.search(""))
        self.assertEqual(search.search("zzzzz"), [])

    def test_hostile_input_never_reaches_the_match_syntax(self):
        for term in ['"', "AND", "corsa AND", "*", "-corsa", "((", 'siena"* OR "', "NEAR(a b)"]:
            with self.subTest(term=term):
                search.search(term)  # não pode levantar OperationalError


class TriggerTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(fipe_code=21, name="Fiat")
        self.model = VehicleModel.objects.create(
            brand=self.brand, fipe_code=1, name="Zumbi Turbo"
        )

    def test_new_model_becomes_searchable(self):
        self.assertEqual(search.search("zumbi"), [self.model.pk])

    def test_renaming_the_model_reindexes_it(self):
        self.model.name = "Fantasma Turbo"
        self.model.save()
        self.assertEqual(search.search("zumbi"), [])
        self.assertEqual(search.search("fantasma"), [self.model.pk])

    def test_renaming_the_brand_reindexes_its_models(self):
        self.brand.name = "Fiat Automóveis"
        self.brand.save()
        self.assertEqual(search.search("automoveis"), [self.model.pk])

    def test_deleting_the_model_removes_it_from_the_index(self):
        self.model.delete()
        self.assertEqual(search.search("zumbi"), [])
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test web.tests.test_search -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.search'`.

- [ ] **Step 4: Escrever o módulo**

`web/search.py`:

```python
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
```

- [ ] **Step 5: Rodar e ver passar**

Run: `./venv/bin/python manage.py test web.tests.test_search -v 2`
Expected: PASS, 12 testes.

- [ ] **Step 6: Commit**

```bash
git add web/search.py web/tests/
git commit -m "feat: Busca full-text isolada em web/search.py"
```

---

## Task 4: `web/filters.py`

**Files:**
- Create: `web/filters.py`
- Create: `web/tests/test_filters.py`

- [ ] **Step 1: Escrever o teste que falha**

`web/tests/test_filters.py` — `SimpleTestCase` porque este módulo não toca o banco:

```python
from django.http import QueryDict
from django.test import SimpleTestCase

from web.filters import SearchFilters


def parse(querystring):
    return SearchFilters.from_query(QueryDict(querystring))


class ParsingTests(SimpleTestCase):
    def test_reads_every_field(self):
        filters = parse("q=corsa&fuel=5&fuel=6&year_op=lte&year=2015&page=3")
        self.assertEqual(filters.term, "corsa")
        self.assertEqual(filters.fuels, (5, 6))
        self.assertEqual(filters.year_op, "lte")
        self.assertEqual(filters.year, 2015)
        self.assertEqual(filters.page, 3)

    def test_empty_query_is_the_default(self):
        filters = parse("")
        self.assertEqual(filters, SearchFilters())
        self.assertTrue(filters.is_empty)

    def test_unknown_operator_falls_back_to_the_default(self):
        self.assertEqual(parse("year_op=drop&year=2015").year_op, "gte")

    def test_non_numeric_year_disables_the_year_filter(self):
        self.assertIsNone(parse("year=ontem&year_op=lte").year)

    def test_non_numeric_fuel_is_dropped_and_repeats_collapse(self):
        self.assertEqual(parse("fuel=5&fuel=x&fuel=5&fuel=6").fuels, (5, 6))

    def test_bad_page_becomes_the_first(self):
        self.assertEqual(parse("page=abc").page, 1)
        self.assertEqual(parse("page=-4").page, 1)


class QuerystringTests(SimpleTestCase):
    def test_round_trip(self):
        original = "q=corsa&fuel=5&fuel=6&year_op=lte&year=2015&page=3"
        self.assertEqual(parse(parse(original).querystring()), parse(original))

    def test_empty_filters_produce_an_empty_querystring(self):
        self.assertEqual(SearchFilters().querystring(), "")

    def test_override_replaces_one_field(self):
        filters = parse("q=corsa&page=3")
        self.assertEqual(filters.querystring(page=4), "q=corsa&page=4")

    def test_first_page_is_left_out_of_the_link(self):
        self.assertEqual(parse("q=corsa&page=3").querystring(page=1), "q=corsa")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test web.tests.test_filters -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.filters'`.

- [ ] **Step 3: Escrever o módulo**

`web/filters.py`:

```python
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
```

- [ ] **Step 4: Rodar e ver passar**

Run: `./venv/bin/python manage.py test web.tests.test_filters -v 2`
Expected: PASS, 10 testes.

- [ ] **Step 5: Commit**

```bash
git add web/filters.py web/tests/test_filters.py
git commit -m "feat: SearchFilters traduz a querystring da busca"
```

---

## Task 5: Código de modelo em `web/codes.py`

**Files:**
- Modify: `web/codes.py`
- Create: `web/tests/factories.py`, `web/tests/test_codes.py`
- Modify: `web/tests/test_views.py`

- [ ] **Step 1: Extrair os helpers para um módulo próprio**

`build_vehicle` e `add_quote` são usados por `test_codes`, `test_queries` e `test_views`, então
saem de `test_views.py` para `web/tests/factories.py` — recortados sem alteração, mais os
imports que eles usam (`Decimal`, `Brand`, `FuelType`, `ModelYear`, `PriceQuote`,
`ReferenceTable`, `VehicleModel`). O nome não começa com `test`, então o runner não o coleta.

Os três módulos de teste passam a importar `from web.tests.factories import add_quote, build_vehicle`.

- [ ] **Step 2: Escrever o teste que falha**

Criar `web/tests/test_codes.py` com a classe `CodeTests` recortada de `web/tests/test_views.py`,
mais:

```python
class ModelCodeTests(TestCase):
    def test_round_trip(self):
        model_year = build_vehicle()
        vehicle_model = model_year.vehicle_model
        code = codes.encode_model(vehicle_model)
        self.assertEqual(code, "1-21-4712")
        self.assertEqual(codes.get_model(code), vehicle_model)

    def test_a_version_code_is_not_a_model_code(self):
        model_year = build_vehicle()
        self.assertIsNone(codes.get_model(codes.encode(model_year)))
        self.assertIsNone(codes.get(codes.encode_model(model_year.vehicle_model)))

    def test_malformed_codes_resolve_to_nothing(self):
        for code in ["", "abc", "1-21", "1-21-4712-2017", "1-21-x"]:
            with self.subTest(code=code):
                self.assertIsNone(codes.get_model(code))
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test web.tests.test_codes -v 2`
Expected: FAIL — `AttributeError: module 'web.codes' has no attribute 'encode_model'`.

- [ ] **Step 4: Implementar**

Acrescentar a `web/codes.py` (e importar `VehicleModel` no topo):

```python
MODEL_PARTS = 3


def encode_model(vehicle_model):
    brand = vehicle_model.brand
    return SEPARATOR.join(
        [str(brand.vehicle_type), str(brand.fipe_code), str(vehicle_model.fipe_code)]
    )


def decode_model(code):
    parts = code.strip().split(SEPARATOR)
    if len(parts) != MODEL_PARTS or not all(part.isdigit() for part in parts):
        return None
    vehicle_type, brand, model = parts
    return {
        "brand__vehicle_type": int(vehicle_type),
        "brand__fipe_code": int(brand),
        "fipe_code": int(model),
    }


def get_model(code):
    """The VehicleModel a code points to, or None."""
    filters = decode_model(code)
    if filters is None:
        return None
    return VehicleModel.objects.select_related("brand").filter(**filters).first()
```

- [ ] **Step 5: Rodar e ver passar**

Run: `./venv/bin/python manage.py test web -v 2`
Expected: PASS. A contagem de partes (3 vs 5) é o que impede um código de versão de resolver
como modelo e vice-versa.

- [ ] **Step 6: Commit**

```bash
git add web/codes.py web/tests/
git commit -m "feat: Código compartilhável de modelo, com 3 partes"
```

---

## Task 6: Agregação por modelo em `web/queries.py`

**Files:**
- Modify: `web/queries.py`
- Create: `web/tests/test_queries.py` (movendo `VariationTests` de `test_views.py`)

- [ ] **Step 1: Escrever o teste que falha**

Em `web/tests/test_queries.py`, além do `VariationTests` movido:

```python
class SearchModelsTests(TestCase):
    def setUp(self):
        self.uno = build_vehicle(model_code=1, year=2015, fuel=FuelType.FLEX)
        self.uno_gas = build_vehicle(model_code=1, year=2015, fuel=FuelType.GASOLINE)
        self.palio = build_vehicle(model_code=2, year=2010, fuel=FuelType.FLEX)
        add_quote(self.uno, 2026, 8, "40000.00")
        add_quote(self.uno_gas, 2026, 8, "38000.00")
        add_quote(self.palio, 2026, 8, "25000.00")

    def test_groups_versions_into_one_card_per_model(self):
        page = queries.search_models(SearchFilters())
        cards = {card["vehicle_model"].fipe_code: card for card in page}
        self.assertEqual(cards[1]["versions"], 2)
        self.assertEqual(cards[1]["min_value"], Decimal("38000.00"))
        self.assertEqual(cards[1]["max_value"], Decimal("40000.00"))

    def test_price_range_uses_only_the_newest_reference(self):
        # Um mês antigo e mais barato não pode puxar a faixa para baixo.
        add_quote(self.uno, 2026, 7, "10000.00")
        page = queries.search_models(SearchFilters())
        card = next(c for c in page if c["vehicle_model"].fipe_code == 1)
        self.assertEqual(card["min_value"], Decimal("38000.00"))

    def test_version_count_respects_the_fuel_filter(self):
        page = queries.search_models(SearchFilters(fuels=(FuelType.FLEX,)))
        card = next(c for c in page if c["vehicle_model"].fipe_code == 1)
        self.assertEqual(card["versions"], 1)

    def test_year_operators(self):
        cases = {("gte", 2015): {1}, ("lte", 2010): {2}, ("eq", 2010): {2}}
        for (op, year), expected in cases.items():
            with self.subTest(op=op, year=year):
                page = queries.search_models(SearchFilters(year_op=op, year=year))
                self.assertEqual({c["vehicle_model"].fipe_code for c in page}, expected)

    def test_zero_km_counts_as_the_newest_year(self):
        zero = build_vehicle(model_code=3, year=ZERO_KM_YEAR, fuel=FuelType.FLEX)
        add_quote(zero, 2026, 8, "90000.00")
        codes_for = lambda op, year: {  # noqa: E731
            c["vehicle_model"].fipe_code
            for c in queries.search_models(SearchFilters(year_op=op, year=year))
        }
        self.assertIn(3, codes_for("gte", 2015))
        self.assertNotIn(3, codes_for("lte", 2015))
        self.assertNotIn(3, codes_for("eq", 2015))
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test web.tests.test_queries -v 2`
Expected: FAIL — `AttributeError: module 'web.queries' has no attribute 'search_models'`.

- [ ] **Step 3: Implementar**

Em `web/queries.py`: apagar `brands()` e `vehicle_models()`, acrescentar os imports
(`Count`, `Max`, `Min` de `django.db.models`, `Paginator` de `django.core.paginator`,
`VehicleModel`, `web.codes`, `web.search`) e:

```python
PER_PAGE = 24


def latest_reference_table():
    return ReferenceTable.objects.first()


def available_fuels():
    """The fuels present in the data, not a fixed list — an unnamed FIPE code
    shows up as a bare number instead of disappearing from the filter."""
    codes_in_use = ModelYear.objects.values_list("fuel_type", flat=True).distinct()
    return sorted(codes_in_use)


def available_years():
    years = ModelYear.objects.exclude(year=ZERO_KM_YEAR).values_list("year", flat=True)
    return sorted(set(years), reverse=True)


def search_models(filters):
    """One page of cards, one card per VehicleModel."""
    reference = latest_reference_table()
    if reference is None:
        return Paginator([], PER_PAGE).get_page(1)

    model_years = ModelYear.objects.all()
    if filters.fuels:
        model_years = model_years.filter(fuel_type__in=filters.fuels)
    if filters.year is not None:
        model_years = model_years.filter(**{filters.year_lookup: filters.year})

    ranked_ids = search.search(filters.term)
    if ranked_ids is not None:
        model_years = model_years.filter(vehicle_model_id__in=ranked_ids)

    rows = (
        PriceQuote.objects.filter(reference_table=reference, model_year__in=model_years)
        .values("model_year__vehicle_model")
        .annotate(
            min_value=Min("value"),
            max_value=Max("value"),
            versions=Count("model_year", distinct=True),
        )
        # PriceQuote.Meta.ordering would otherwise join the GROUP BY and split
        # each model into one row per reference month.
        .order_by()
    )

    if ranked_ids is None:
        rows = rows.order_by(
            "model_year__vehicle_model__brand__name", "model_year__vehicle_model__name"
        )
        page = Paginator(rows, PER_PAGE).get_page(filters.page)
    else:
        position = {pk: index for index, pk in enumerate(ranked_ids)}
        ordered = sorted(rows, key=lambda row: position[row["model_year__vehicle_model"]])
        page = Paginator(ordered, PER_PAGE).get_page(filters.page)

    # Only the page's models are loaded, never the whole result.
    ids = [row["model_year__vehicle_model"] for row in page]
    models = VehicleModel.objects.select_related("brand").in_bulk(ids)
    page.object_list = [
        row
        | {
            "vehicle_model": models[row["model_year__vehicle_model"]],
            "code": codes.encode_model(models[row["model_year__vehicle_model"]]),
            "reference_table": reference,
        }
        for row in page
    ]
    return page


def model_versions(vehicle_model):
    """Every version of a model, newest first — the model page ignores the
    search filters, so its URL always shows the same thing."""
    reference = latest_reference_table()
    return (
        ModelYear.objects.filter(vehicle_model=vehicle_model)
        .select_related("vehicle_model__brand")
        .prefetch_related(
            Prefetch(
                "quotes",
                queryset=PriceQuote.objects.filter(reference_table=reference),
                to_attr="current_quotes",
            )
        )
        .order_by("-year", "fuel_type")
    )
```

Importar também `Prefetch` de `django.db.models` e `ZERO_KM_YEAR` de `crawler.models`.

- [ ] **Step 4: Rodar e ver passar**

Run: `./venv/bin/python manage.py test web.tests.test_queries -v 2`
Expected: PASS. Se `test_groups_versions_into_one_card_per_model` acusar 2 cards para o mesmo
modelo, o `.order_by()` vazio foi esquecido.

- [ ] **Step 5: Commit**

```bash
git add web/queries.py web/tests/
git commit -m "feat: Agregação de cards por modelo, com faixa de preço do mês vigente"
```

---

## Task 7: Tela de busca

**Files:**
- Modify: `web/views.py`, `web/urls.py`
- Rewrite: `web/templates/web/home.html`
- Create: `web/templates/web/partials/results.html`, `web/templates/web/partials/model_card.html`
- Delete: `web/templates/web/partials/search_form.html`, `cascade_tail.html`, `year_select.html`

- [ ] **Step 1: Escrever o teste que falha**

Em `web/tests/test_views.py`, substituir a classe `SearchTests` por:

```python
class SearchScreenTests(TestCase):
    def setUp(self):
        self.uno = build_vehicle(model_code=1, year=2015, fuel=FuelType.FLEX)
        self.uno.vehicle_model.name = "Uno Mille Fire"
        self.uno.vehicle_model.save()
        add_quote(self.uno, 2026, 8, "40000.00")

    def test_lists_every_model_without_a_term(self):
        response = self.client.get(reverse("web:home"))
        self.assertContains(response, "Uno Mille Fire")
        self.assertContains(response, "R$ 40.000,00")

    def test_finds_by_term(self):
        self.assertContains(self.client.get(reverse("web:home"), {"q": "mille"}), "Uno Mille")

    def test_term_without_matches_shows_the_empty_state(self):
        response = self.client.get(reverse("web:home"), {"q": "lamborghini"})
        self.assertContains(response, "Nenhum modelo encontrado")

    def test_filters_survive_in_the_links(self):
        response = self.client.get(reverse("web:home"), {"q": "mille", "fuel": "5"})
        self.assertEqual(response.context["filters"].fuels, (5,))
        self.assertContains(response, 'value="mille"')

    def test_htmx_request_returns_only_the_results(self):
        response = self.client.get(
            reverse("web:home"), {"q": "mille"}, headers={"hx-request": "true"}
        )
        self.assertContains(response, "Uno Mille")
        self.assertNotContains(response, "<html")

    def test_cascade_endpoints_are_gone(self):
        for name in ["web:model_options", "web:year_options"]:
            with self.subTest(name=name):
                with self.assertRaises(NoReverseMatch):
                    reverse(name)
```

Importar `NoReverseMatch` de `django.urls` e `FuelType` de `crawler.models`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test web.tests.test_views -v 2`
Expected: FAIL — a home ainda renderiza a cascata e não contém "Uno Mille Fire" em card.

- [ ] **Step 3: Reescrever a view `home`**

Em `web/views.py`, remover `model_options`, `year_options`, `_field`, `FIELD_NAMES`, `_int`
(passou para `filters.py`) e `_base_context` (chamava `queries.brands()`, que deixou de
existir), e escrever:

```python
YEAR_OPS = [("gte", "a partir de"), ("eq", "exatamente"), ("lte", "até")]
FUEL_LABELS = dict(FuelType.choices)


def _search_context(filters):
    """Tudo que a tela de busca precisa. Usada também pelas telas que caem de
    volta na busca quando o código da URL não resolve."""
    page = queries.search_models(filters)
    return {
        "filters": filters,
        "page": page,
        "fuels": [
            (code, FUEL_LABELS.get(code, f"Combustível {code}"))
            for code in queries.available_fuels()
        ],
        "years": queries.available_years(),
        "year_ops": YEAR_OPS,
        "previous_url": (
            filters.querystring(page=page.previous_page_number()) if page.has_previous() else ""
        ),
        "next_url": (
            filters.querystring(page=page.next_page_number()) if page.has_next() else ""
        ),
        "reference_table": queries.latest_reference_table(),
    }


def home(request):
    filters = SearchFilters.from_query(request.GET)
    context = _search_context(filters)
    if request.headers.get("HX-Request"):
        return render(request, "web/partials/results.html", context)
    return render(request, "web/home.html", context)
```

E, no topo do módulo:

```python
from crawler.models import FuelType

from web.filters import SearchFilters
```

**Os outros usuários de `_base_context` precisam acompanhar.** `detail` e `compare` renderizavam
`web/home.html` (ou o `base.html`) com o contexto antigo; o `home.html` novo exige o contexto de
busca. Em `detail`, o caminho de "não encontrado" passa a ser:

```python
    if model_year is None:
        return render(
            request,
            "web/home.html",
            _search_context(SearchFilters()) | {"message": "Veículo não encontrado."},
            status=404,
        )
```

E o resto de `detail` e `compare` troca `_base_context()` por
`{"reference_table": queries.latest_reference_table()}`, que é a única chave que essas telas
realmente usavam.

- [ ] **Step 4: Escrever os templates**

`web/templates/web/partials/model_card.html`:

```html
{% load formatting %}
<a href="{% url 'web:model' %}?m={{ card.code }}"
   class="block rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-blue-300 hover:shadow-md">
  <p class="text-xs font-medium text-blue-600">{{ card.vehicle_model.brand.name }}</p>
  <h3 class="mt-1 font-semibold text-slate-900">{{ card.vehicle_model.name }}</h3>
  <p class="mt-3 text-lg font-semibold tracking-tight text-slate-900">
    {% if card.min_value == card.max_value %}
      {{ card.min_value|brl }}
    {% else %}
      {{ card.min_value|brl }} <span class="text-slate-400">–</span> {{ card.max_value|brl }}
    {% endif %}
  </p>
  <p class="mt-1 text-xs text-slate-500">
    {{ card.versions }} versã{{ card.versions|pluralize:"o,es" }} · {{ card.reference_table }}
  </p>
</a>
```

`web/templates/web/partials/results.html`:

```html
{% if page.object_list %}
  <p class="mb-4 text-sm text-slate-500">
    {{ page.paginator.count }} modelo{{ page.paginator.count|pluralize }} encontrado{{ page.paginator.count|pluralize }}
  </p>
  <div class="grid gap-4 sm:grid-cols-2">
    {% for card in page %}{% include "web/partials/model_card.html" %}{% endfor %}
  </div>
  {% if page.has_other_pages %}
    <nav class="mt-6 flex items-center justify-between text-sm">
      {% if previous_url %}
        <a href="?{{ previous_url }}" class="font-medium text-blue-600 hover:underline">← anteriores</a>
      {% else %}<span></span>{% endif %}
      <span class="text-slate-500">página {{ page.number }} de {{ page.paginator.num_pages }}</span>
      {% if next_url %}
        <a href="?{{ next_url }}" class="font-medium text-blue-600 hover:underline">próximos →</a>
      {% else %}<span></span>{% endif %}
    </nav>
  {% endif %}
{% else %}
  <div class="rounded-2xl border border-dashed border-slate-300 bg-white/60 px-6 py-12 text-center">
    <p class="font-medium text-slate-700">Nenhum modelo encontrado</p>
    <p class="mt-1 text-sm text-slate-500">
      {% if filters.is_empty %}
        O banco ainda não tem cotações. Rode <code class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">python manage.py crawl_fipe</code>.
      {% else %}
        Tente outro termo ou afrouxe os filtros.
      {% endif %}
    </p>
    {% if not filters.is_empty %}
      <a href="{% url 'web:home' %}" class="mt-4 inline-block text-sm font-medium text-blue-600 hover:underline">limpar filtros</a>
    {% endif %}
  </div>
{% endif %}
```

`web/templates/web/home.html`:

```html
{% extends "web/base.html" %}

{% block title %}Buscar{% endblock %}

{% block content %}
  <h1 class="text-3xl font-semibold tracking-tight text-slate-900">Buscar veículo</h1>
  <p class="mt-2 text-slate-600">Digite o modelo. O preço vem da Tabela FIPE, com o histórico já coletado.</p>

  <form action="{% url 'web:home' %}" method="get"
        hx-get="{% url 'web:home' %}" hx-target="#results" hx-swap="innerHTML"
        hx-push-url="true" hx-trigger="submit, change from:#filters"
        class="mt-6">
    <div class="flex gap-2">
      <input type="search" name="q" value="{{ filters.term }}" placeholder="corsa, uno mille, siena…"
             class="w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-slate-900 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30">
      <button type="submit" class="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-700">Buscar</button>
    </div>

    <div class="mt-6 grid gap-8 lg:grid-cols-[15rem_1fr]">
      <aside id="filters" class="space-y-6">
        <fieldset>
          <legend class="text-sm font-medium text-slate-700">Combustível</legend>
          <div class="mt-2 space-y-1.5">
            {% for code, label in fuels %}
              <label class="flex items-center gap-2 text-sm text-slate-600">
                <input type="checkbox" name="fuel" value="{{ code }}"
                       {% if code in filters.fuels %}checked{% endif %}
                       class="rounded border-slate-300 text-blue-600 focus:ring-blue-500/30">
                {{ label }}
              </label>
            {% endfor %}
          </div>
        </fieldset>

        <fieldset>
          <legend class="text-sm font-medium text-slate-700">Ano</legend>
          <div class="mt-2 flex gap-2">
            <select name="year_op" class="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm">
              {% for value, label in year_ops %}
                <option value="{{ value }}" {% if filters.year_op == value %}selected{% endif %}>{{ label }}</option>
              {% endfor %}
            </select>
            <select name="year" class="w-full rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm">
              <option value="">qualquer</option>
              {% for year in years %}
                <option value="{{ year }}" {% if filters.year == year %}selected{% endif %}>{{ year }}</option>
              {% endfor %}
            </select>
          </div>
        </fieldset>
      </aside>

      <div id="results">{% include "web/partials/results.html" %}</div>
    </div>
  </form>
{% endblock %}
```

- [ ] **Step 5: Apagar a cascata**

```bash
git rm web/templates/web/partials/search_form.html \
       web/templates/web/partials/cascade_tail.html \
       web/templates/web/partials/year_select.html
```

E remover de `web/urls.py` as rotas `model_options` e `year_options`, acrescentando:

```python
    path("modelo/", views.model_detail, name="model"),
```

- [ ] **Step 6: Rodar e ver passar**

Run: `./venv/bin/python manage.py test web.tests.test_views -v 2`
Expected: PASS (a Task 8 fecha a view `model_detail`, então rode esta etapa depois dela se o
`{% url 'web:model' %}` do card acusar `NoReverseMatch`).

- [ ] **Step 7: Commit**

```bash
git add -A web/
git commit -m "feat: Tela de busca com filtros e cards"
```

---

## Task 8: Página do modelo

**Files:**
- Modify: `web/views.py`
- Create: `web/templates/web/model.html`

- [ ] **Step 1: Escrever o teste que falha**

Em `web/tests/test_views.py`:

```python
class ModelPageTests(TestCase):
    def setUp(self):
        self.flex = build_vehicle(model_code=1, year=2015, fuel=FuelType.FLEX)
        self.old = build_vehicle(model_code=1, year=2005, fuel=FuelType.GASOLINE)
        add_quote(self.flex, 2026, 8, "40000.00")
        add_quote(self.old, 2026, 8, "18000.00")
        self.code = codes.encode_model(self.flex.vehicle_model)

    def test_lists_every_version_newest_first(self):
        response = self.client.get(reverse("web:model"), {"m": self.code})
        self.assertContains(response, "R$ 40.000,00")
        self.assertContains(response, "R$ 18.000,00")
        versions = response.context["versions"]
        self.assertEqual([v.year for v in versions], [2015, 2005])

    def test_ignores_the_search_filters(self):
        # A página é sobre o modelo: o mesmo link não pode mostrar coisas
        # diferentes conforme a busca que levou até ele.
        response = self.client.get(reverse("web:model"), {"m": self.code, "fuel": "5"})
        self.assertEqual(len(response.context["versions"]), 2)

    def test_unknown_model_returns_to_the_search(self):
        response = self.client.get(reverse("web:model"), {"m": "1-99-99"})
        self.assertEqual(response.status_code, 404)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test web.tests.test_views.ModelPageTests -v 2`
Expected: FAIL — `AttributeError: module 'web.views' has no attribute 'model_detail'`.

- [ ] **Step 3: Implementar a view**

```python
def model_detail(request):
    vehicle_model = codes.get_model(request.GET.get("m", ""))
    if vehicle_model is None:
        return render(
            request,
            "web/home.html",
            _search_context(SearchFilters()) | {"message": "Modelo não encontrado."},
            status=404,
        )
    return render(
        request,
        "web/model.html",
        {
            "vehicle_model": vehicle_model,
            "versions": queries.model_versions(vehicle_model),
            "reference_table": queries.latest_reference_table(),
            "back_to_search": request.GET.get("from", ""),
        },
    )
```

`_search_context` já existe desde a Task 7 — é o mesmo helper, sem duplicação.

- [ ] **Step 4: Escrever o template**

`web/templates/web/model.html`:

```html
{% extends "web/base.html" %}
{% load formatting %}

{% block title %}{{ vehicle_model.name }}{% endblock %}

{% block content %}
  <a href="{% url 'web:home' %}{% if back_to_search %}?{{ back_to_search }}{% endif %}"
     class="text-sm font-medium text-slate-500 hover:text-slate-900">← voltar à busca</a>

  <p class="mt-4 text-sm font-medium text-blue-600">{{ vehicle_model.brand.name }}</p>
  <h1 class="mt-1 text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">{{ vehicle_model.name }}</h1>

  <div class="mt-6 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
    <table class="w-full text-sm">
      <thead class="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
        <tr>
          <th class="p-4">Ano</th><th class="p-4">Combustível</th>
          <th class="p-4">Preço</th><th class="p-4"></th>
        </tr>
      </thead>
      <tbody>
        {% for version in versions %}
          <tr class="border-b border-slate-100 last:border-0">
            <td class="p-4 font-medium text-slate-900">{{ version|model_year_label }}</td>
            <td class="p-4 text-slate-600">{{ version.get_fuel_type_display }}</td>
            <td class="p-4 font-semibold text-slate-900">
              {% for quote in version.current_quotes %}{{ quote.value|brl }}{% empty %}—{% endfor %}
            </td>
            <td class="p-4 text-right whitespace-nowrap">
              <a href="{% url 'web:detail' %}?v={{ version|code }}" class="font-medium text-blue-600 hover:underline">histórico</a>
              <a href="{% url 'web:compare' %}?v={{ version|code }}" class="ml-3 font-medium text-slate-500 hover:text-slate-900">+ comparar</a>
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
{% endblock %}
```

- [ ] **Step 5: Rodar e ver passar**

Run: `./venv/bin/python manage.py test web -v 2`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A web/
git commit -m "feat: Página do modelo com todas as versões"
```

---

## Task 9: Ajustar o comparador

**Files:**
- Modify: `web/templates/web/compare.html`, `web/views.py`
- Modify: `web/tests/test_views.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
class CompareEntryTests(TestCase):
    def test_compare_page_no_longer_carries_a_picker(self):
        response = self.client.get(reverse("web:compare"))
        self.assertNotContains(response, 'name="add"')
        self.assertContains(response, "Buscar veículos")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `./venv/bin/python manage.py test web.tests.test_views.CompareEntryTests -v 2`
Expected: FAIL — `TemplateDoesNotExist: web/partials/search_form.html` (o include ainda existe).

- [ ] **Step 3: Trocar o include pelo link**

Em `web/templates/web/compare.html`, substituir o bloco `{% if not is_full %}…{% endif %}` por:

```html
  {% if is_full %}
    <p class="mt-8 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
      Limite de {{ max_compared }} versões atingido. Remova uma para incluir outra.
    </p>
  {% else %}
    <p class="mt-8">
      <a href="{% url 'web:home' %}?{{ selection_query }}"
         class="rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700">
        Buscar veículos
      </a>
    </p>
  {% endif %}
```

Em `compare`, acrescentar ao contexto `"selection_query": urlencode({"v": ",".join(kept)})`
(de `django.utils.http`). A troca de `_base_context` já aconteceu na Task 7.

- [ ] **Step 4: Rodar tudo**

Run: `./venv/bin/python manage.py test`
Expected: PASS, crawler e web.

- [ ] **Step 5: Commit**

```bash
git add -A web/
git commit -m "refactor: Comparador entra pela busca, sem picker próprio"
```

---

## Task 10: Documentação e verificação no navegador

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Reconstruir o CSS**

```bash
./tailwindcss -i web/static/web/src/input.css -o web/static/web/app.css --minify
```
Expected: as classes novas dos cards e da barra lateral entram no `app.css`. Sem isso a tela
sobe sem estilo.

- [ ] **Step 2: Conferir no navegador**

```bash
./venv/bin/python manage.py runserver
```
Percorrer: buscar "siena"; marcar Flex; pôr ano "a partir de 2015"; abrir um card; mandar duas
versões para o comparador; copiar a URL da busca e reabrir numa aba nova (os filtros têm que
voltar iguais).

- [ ] **Step 3: Atualizar o CLAUDE.md**

Reescrever a seção **Web** (a cascata não existe mais), acrescentar `search.py` e `filters.py`
à **Estrutura**, e registrar em **Estado atual** que os combustíveis 6 e 7 foram nomeados.
Documentar as três armadilhas que custariam meia hora a quem mexer depois:

- a tabela FTS5 é autônoma e não `content=`, porque `brand` é FK e não coluna;
- existe trigger em `crawler_brand` porque `sync.py` renomeia marca;
- `values().annotate()` sobre `PriceQuote` precisa de `.order_by()` vazio, senão o
  `Meta.ordering` entra no `GROUP BY` e parte cada modelo em uma linha por mês.

- [ ] **Step 4: Verificação final**

```bash
./venv/bin/python manage.py test
./venv/bin/python manage.py check
```
Expected: tudo verde, nenhum aviso.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md web/static/web/app.css
git commit -m "docs: Atualiza o CLAUDE.md para a busca full-text"
```

---

## Riscos conhecidos

- **O Tailwind precisa ser reconstruído** (Task 10, Step 1) — comando do Jorge, não meu.
- **`ruff` não está no venv**, então o `ruff check .` do CLAUDE.md continua sem rodar; seguir o
  estilo do `crawler/` na mão.
- **A tabela FTS5 é invisível para o ORM.** Escrita em SQL cru que não passe pelos triggers
  dessincroniza o índice. O caminho normal (ORM e crawler) está coberto; um comando de
  reconstrução fica deliberadamente fora de escopo.
- **"corsa" não devolve nada hoje** — a GM só tem 23 modelos coletados, parando no "AGILE".
  Não é falha da busca; para testar com um caso real use "siena", "uno" ou "aircross".
