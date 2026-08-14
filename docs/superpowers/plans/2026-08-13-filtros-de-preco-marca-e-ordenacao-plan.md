# Filtros de preço e marca, e ordenação por preço — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Acrescentar à busca um filtro de preço (operador + valor), um filtro de marca e a ordenação por preço, conforme `docs/superpowers/specs/2026-08-13-filtros-de-preco-marca-e-ordenacao-design.md`.

**Architecture:** Nenhum módulo novo. `web/codes.py` ganha o código de marca de duas partes; `web/filters.py` ganha quatro campos na dataclass; `web/queries.py` aplica os filtros e a ordenação; `web/views.py` e `home.html` expõem os controles dentro do `#filters` que o HTMX já observa.

**Tech Stack:** Django 6.1, Python 3.14, runner de testes do Django. Sem dependência nova.

---

## Antes de começar

**O shell do usuário é fish.** `source venv/bin/activate.fish`, ou chame `./venv/bin/python` direto, como os comandos abaixo fazem.

**Como rodar os testes.** O default das settings aponta para o Postgres do `compose.yml`, e `psycopg` ainda não está instalado. Enquanto isso, rode com o override de SQLite:

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test web
```

Quando `psycopg` estiver instalado e o container de pé, **rode as duas vezes** — sem override (Postgres) e com override (SQLite). Compatibilidade entre engines é invariante do projeto, e nada neste plano é específico de banco.

**Regras do projeto que valem aqui:** identificadores e comentários em inglês, texto visível ao usuário em português, nunca instalar pacotes.

---

## Estrutura de arquivos

| arquivo | responsabilidade | o que muda |
|---|---|---|
| `web/codes.py` | Identificadores compartilháveis | Ganha `encode_brand`, `decode_brand`, `get_brand` |
| `web/filters.py` | Querystring ↔ dataclass, sem tocar em models | Ganha `brand`, `price_op`, `price`, `sort` e as constantes de preço |
| `web/queries.py` | Toda leitura do banco | Ganha `available_brands`, `SORT_ORDERS` e a aplicação dos filtros |
| `web/views.py` | Rótulos em português e montagem do contexto | Ganha `PRICE_OPS`, `SORT_OPTIONS` e os degraus formatados |
| `web/templates/web/home.html` | Barra lateral | Ganha três `<fieldset>` dentro de `#filters` |
| `web/tests/factories.py` | Construtores dos testes | `build_vehicle` passa a aceitar `brand_name` |
| `CLAUDE.md` | Documentação viva | Ganha a semântica do filtro de preço e o código de marca |

---

## Task 1: Código de marca no `codes.py`

**Files:**
- Modify: `web/codes.py`
- Test: `web/tests/test_codes.py`

- [ ] **Step 1: Escreva o teste que falha**

Acrescente ao fim de `web/tests/test_codes.py`:

```python
class BrandCodeTests(TestCase):
    def test_round_trip(self):
        model_year = build_vehicle()
        brand = model_year.vehicle_model.brand
        code = codes.encode_brand(brand)
        self.assertEqual(code, "1-21")
        self.assertEqual(codes.get_brand(code), brand)

    def test_the_other_codes_are_not_brand_codes(self):
        # A contagem de partes é o que impede um código de resolver como outro.
        model_year = build_vehicle()
        self.assertIsNone(codes.get_brand(codes.encode_model(model_year.vehicle_model)))
        self.assertIsNone(codes.get_brand(codes.encode(model_year)))

    def test_malformed_codes_resolve_to_nothing(self):
        for code in ["", "abc", "1", "1-21-4712", "1-x"]:
            with self.subTest(code=code):
                self.assertIsNone(codes.get_brand(code))
```

- [ ] **Step 2: Rode o teste e confirme que ele falha**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test web.tests.test_codes.BrandCodeTests
```

Esperado: FAIL com `AttributeError: module 'web.codes' has no attribute 'encode_brand'`.

- [ ] **Step 3: Implemente**

Em `web/codes.py`, acrescente `Brand` aos imports do topo:

```python
from crawler.models import Brand
from crawler.models import ModelYear
from crawler.models import VehicleModel
```

Acrescente `BRAND_PARTS` junto das outras constantes:

```python
SEPARATOR = "-"
PARTS = 5
MODEL_PARTS = 3
BRAND_PARTS = 2
```

E as três funções ao fim do arquivo, antes de `parse_list`:

```python
def encode_brand(brand):
    return SEPARATOR.join([str(brand.vehicle_type), str(brand.fipe_code)])


def decode_brand(code):
    """Same discipline as the other codes, two parts this time.

    The vehicle type is not decoration: FIPE numbers its brands per type, so a
    bare 21 would mean one brand for cars and another for motorcycles the day
    motorcycles are collected.
    """
    parts = code.strip().split(SEPARATOR)
    if len(parts) != BRAND_PARTS or not all(part.isdigit() for part in parts):
        return None
    vehicle_type, fipe_code = parts
    return {"vehicle_type": int(vehicle_type), "fipe_code": int(fipe_code)}


def get_brand(code):
    """The Brand a code points to, or None."""
    filters = decode_brand(code)
    if filters is None:
        return None
    return Brand.objects.filter(**filters).first()
```

Atualize também o docstring do módulo, que hoje só descreve o código de versão. Substitua o bloco de exemplo por:

```python
"""Shareable identifiers for a brand, a model and a model/year version.

A primary key would work in a URL, but only against this database — and the
comparison link is meant to survive being pasted somewhere else. So the codes are
built out of the FIPE codes instead, and told apart by how many parts they have:

    ``1-21``               -> car, brand 21
    ``1-21-4712``          -> car, brand 21, model 4712
    ``1-21-4712-2017-5``   -> the same model, year 2017, flex

The leading vehicle type keeps them unambiguous once motorcycles and trucks are
collected: FIPE numbers its brands per type, so brand 21 means one thing for cars
and another for motorcycles.
"""
```

- [ ] **Step 4: Rode o teste e confirme que passa**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test web.tests.test_codes
```

Esperado: OK, 9 testes (3 de versão, 3 de modelo, 3 de marca).

- [ ] **Step 5: Commit**

```bash
git add web/codes.py web/tests/test_codes.py
git commit -m "feat: Código compartilhável de marca, com 2 partes"
```

---

## Task 2: `SearchFilters` ganha preço, marca e ordenação

**Files:**
- Modify: `web/filters.py`
- Test: `web/tests/test_filters.py`

- [ ] **Step 1: Escreva os testes que falham**

Em `web/tests/test_filters.py`, acrescente à classe `ParsingTests`:

```python
    def test_reads_the_brand_price_and_sort_fields(self):
        filters = parse("brand=1-21&price_op=gte&price=50000&sort=price_desc")
        self.assertEqual(filters.brand, "1-21")
        self.assertEqual(filters.price_op, "gte")
        self.assertEqual(filters.price, 50000)
        self.assertEqual(filters.sort, "price_desc")

    def test_price_defaults_to_at_most(self):
        # Ano se procura "a partir de"; preço, por teto de orçamento.
        self.assertEqual(parse("").price_op, "lte")

    def test_unknown_price_operator_falls_back_to_the_default(self):
        # "eq" existe para ano e não para preço.
        self.assertEqual(parse("price_op=eq&price=50000").price_op, "lte")

    def test_non_positive_or_non_numeric_price_disables_the_filter(self):
        for querystring in ["price=barato", "price=-5", "price=0"]:
            with self.subTest(querystring=querystring):
                self.assertIsNone(parse(querystring).price)

    def test_a_price_outside_the_steps_is_honoured(self):
        # O CLAUDE.md promete que a URL volta igual numa aba nova.
        self.assertEqual(parse("price=43500").price, 43500)

    def test_unknown_sort_is_dropped(self):
        self.assertEqual(parse("sort=drop").sort, "")

    def test_sorting_alone_still_counts_as_an_empty_filter(self):
        # Ordenar não é filtrar: a tela vazia continua dizendo "colete dados".
        self.assertTrue(parse("sort=price_asc").is_empty)
```

E troque o `test_round_trip` de `QuerystringTests` por:

```python
    def test_round_trip(self):
        original = (
            "q=corsa&brand=1-21&fuel=5&fuel=6&year_op=lte&year=2015"
            "&price_op=gte&price=50000&sort=price_asc&page=3"
        )
        self.assertEqual(parse(parse(original).querystring()), parse(original))
```

- [ ] **Step 2: Rode os testes e confirme que falham**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test web.tests.test_filters
```

Esperado: FAIL com `TypeError: SearchFilters.__init__() got an unexpected keyword argument` ou `AttributeError: 'SearchFilters' object has no attribute 'brand'`.

- [ ] **Step 3: Implemente**

Em `web/filters.py`, acrescente as constantes logo abaixo de `DEFAULT_YEAR_OP`:

```python
# Operator -> ORM lookup. The 0 km year (32000) needs no special case: `gte`
# includes it, `eq` and `lte` exclude it, which is exactly the intended meaning.
YEAR_LOOKUPS = {"gte": "year__gte", "eq": "year", "lte": "year__lte"}
DEFAULT_YEAR_OP = "gte"

# These land on PriceQuote.value, not on ModelYear like the year ones. The module
# still imports no models, so they are only strings — queries.py is what knows
# which queryset each one belongs to.
PRICE_LOOKUPS = {"gte": "value__gte", "lte": "value__lte"}
# "até", unlike the year's "a partir de": price is searched as a budget ceiling.
DEFAULT_PRICE_OP = "lte"

# There is no "exatamente" for price on purpose: with fixed steps it would match
# only the exact amount — almost nothing — and read as a bug.

# Reais, not thousands. Only the label on screen abbreviates.
PRICE_STEPS = [10_000, 20_000, 30_000, 50_000, 75_000, 100_000, 150_000, 200_000]

SORTS = ("price_asc", "price_desc")
```

Troque os campos da dataclass por:

```python
@dataclasses.dataclass(frozen=True)
class SearchFilters:
    term: str = ""
    brand: str = ""
    fuels: tuple = ()
    year_op: str = DEFAULT_YEAR_OP
    year: int | None = None
    price_op: str = DEFAULT_PRICE_OP
    price: int | None = None
    sort: str = ""
    page: int = 1
```

Troque `from_query` por:

```python
    @classmethod
    def from_query(cls, query):
        fuels = []
        for raw in query.getlist("fuel"):
            code = _int(raw)
            if code is not None and code not in fuels:
                fuels.append(code)
        year_op = query.get("year_op", DEFAULT_YEAR_OP)
        price_op = query.get("price_op", DEFAULT_PRICE_OP)
        price = _int(query.get("price"))
        sort = query.get("sort", "")
        page = _int(query.get("page"), 1)
        return cls(
            term=query.get("q", "").strip(),
            brand=query.get("brand", "").strip(),
            fuels=tuple(fuels),
            year_op=year_op if year_op in YEAR_LOOKUPS else DEFAULT_YEAR_OP,
            year=_int(query.get("year")),
            price_op=price_op if price_op in PRICE_LOOKUPS else DEFAULT_PRICE_OP,
            # A step that is not on the list is kept: a shared URL has to come
            # back showing what it was sharing.
            price=price if price and price > 0 else None,
            sort=sort if sort in SORTS else "",
            page=page if page and page > 0 else 1,
        )
```

Troque `is_empty` e acrescente `price_lookup`:

```python
    @property
    def is_empty(self):
        """Whether anything was actually asked. Sorting does not count — it
        narrows nothing, and the empty state has to keep saying "collect data"
        instead of "loosen the filters"."""
        return (
            not self.term
            and not self.brand
            and not self.fuels
            and self.year is None
            and self.price is None
        )

    @property
    def year_lookup(self):
        return YEAR_LOOKUPS[self.year_op]

    @property
    def price_lookup(self):
        return PRICE_LOOKUPS[self.price_op]
```

Troque o dicionário `fields` de `querystring` por:

```python
        fields = {
            "q": self.term,
            "brand": self.brand,
            "fuel": list(self.fuels),
            "year_op": self.year_op if self.year is not None else "",
            "year": self.year,
            "price_op": self.price_op if self.price is not None else "",
            "price": self.price,
            "sort": self.sort,
            "page": self.page,
        }
```

- [ ] **Step 4: Rode os testes e confirme que passam**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test web.tests.test_filters
```

Esperado: OK, 17 testes.

- [ ] **Step 5: Commit**

```bash
git add web/filters.py web/tests/test_filters.py
git commit -m "feat: SearchFilters traduz marca, preço e ordenação da querystring"
```

---

## Task 3: `available_brands()` para a barra lateral

**Files:**
- Modify: `web/queries.py`, `web/tests/factories.py`
- Test: `web/tests/test_queries.py`

- [ ] **Step 1: Deixe o construtor nomear a marca**

Em `web/tests/factories.py`, troque a assinatura e a primeira linha de `build_vehicle`:

```python
def build_vehicle(
    brand_code=21, model_code=4712, year=2017, fuel=FuelType.FLEX, brand_name="Fiat"
):
    brand, _ = Brand.objects.get_or_create(fipe_code=brand_code, defaults={"name": brand_name})
```

- [ ] **Step 2: Escreva o teste que falha**

Em `web/tests/test_queries.py`, acrescente à classe `AvailableFacetsTests`:

```python
    def test_brands_are_offered_once_and_only_when_priced(self):
        # Mais estrito que os outros facets de propósito: entre ~100 marcas, uma
        # opção que não devolve nada vira lista de decepções.
        priced = build_vehicle(brand_code=21, model_code=1, year=2015)
        build_vehicle(brand_code=21, model_code=2, year=2016)
        build_vehicle(brand_code=13, model_code=3, year=2015, brand_name="Citroën")
        add_quote(priced, 2026, 8, "40000.00")

        self.assertEqual([brand.fipe_code for brand in queries.available_brands()], [21])
```

- [ ] **Step 3: Rode o teste e confirme que falha**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test web.tests.test_queries.AvailableFacetsTests
```

Esperado: FAIL com `AttributeError: module 'web.queries' has no attribute 'available_brands'`.

- [ ] **Step 4: Implemente**

Em `web/queries.py`, acrescente `Brand` aos imports:

```python
from crawler.models import Brand
from crawler.models import ModelYear
```

E a função logo depois de `available_years()`:

```python
def available_brands():
    """Brands with at least one quote in the newest reference table.

    Deliberately stricter than available_fuels() and available_years(), which
    offer everything present in the data: among 7 fuels a dead option goes
    unnoticed, among ~100 brands it turns the sidebar into a parade of
    disappointments.

    `models` is the related_name of VehicleModel.brand. No empty order_by() here
    because this returns whole Brand rows, not values() — Meta.ordering ["name"]
    is already in the SELECT, so DISTINCT means what it looks like it means.
    """
    reference = latest_reference_table()
    if reference is None:
        return []
    return list(
        Brand.objects.filter(models__model_years__quotes__reference_table=reference).distinct()
    )
```

- [ ] **Step 5: Rode o teste e confirme que passa**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test web.tests.test_queries
```

Esperado: OK.

- [ ] **Step 6: Commit**

```bash
git add web/queries.py web/tests/factories.py web/tests/test_queries.py
git commit -m "feat: available_brands oferece só marcas com cotação no mês vigente"
```

---

## Task 4: Filtro de preço

**Files:**
- Modify: `web/queries.py`
- Test: `web/tests/test_queries.py`

- [ ] **Step 1: Escreva os testes que falham**

Em `web/tests/test_queries.py`, acrescente à classe `SearchModelsTests`. O `setUp` dela já cria o Uno com versões de 38.000 e 40.000 (modelo 1) e o Palio de 25.000 (modelo 2):

```python
    def test_price_ceiling_keeps_a_model_with_one_version_in_range(self):
        page = queries.search_models(SearchFilters(price_op="lte", price=39000))
        self.assertEqual({c["vehicle_model"].fipe_code for c in page}, {1, 2})

    def test_the_card_shrinks_to_the_versions_that_matched(self):
        # Mesma semântica que combustível e ano já têm: o card mostra o que casou.
        page = queries.search_models(SearchFilters(price_op="lte", price=39000))
        card = next(c for c in page if c["vehicle_model"].fipe_code == 1)
        self.assertEqual(card["min_value"], Decimal("38000.00"))
        self.assertEqual(card["max_value"], Decimal("38000.00"))
        self.assertEqual(card["versions"], 1)

    def test_price_floor_drops_the_cheaper_model(self):
        page = queries.search_models(SearchFilters(price_op="gte", price=39000))
        self.assertEqual({c["vehicle_model"].fipe_code for c in page}, {1})
```

- [ ] **Step 2: Rode os testes e confirme que falham**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test web.tests.test_queries.SearchModelsTests
```

Esperado: FAIL — os três devolvem `{1, 2}` sem filtrar, então `test_the_card_shrinks_to_the_versions_that_matched` e `test_price_floor_drops_the_cheaper_model` falham nos valores.

- [ ] **Step 3: Implemente**

Em `web/queries.py`, dentro de `search_models`, troque o bloco que monta `rows`:

```python
    # Price lives on PriceQuote, not on ModelYear, so it narrows the quotes
    # instead of the versions. A model shows up when it has at least one version
    # in range, and the card's range and count then describe only the versions
    # that matched — which is what fuel and year already do.
    quotes = PriceQuote.objects.filter(reference_table=reference, model_year__in=model_years_qs)
    if filters.price is not None:
        quotes = quotes.filter(**{filters.price_lookup: filters.price})

    rows = (
        quotes.values("model_year__vehicle_model")
        .annotate(
            min_value=Min("value"),
            max_value=Max("value"),
            versions=Count("model_year", distinct=True),
        )
        # PriceQuote.Meta.ordering would otherwise join the GROUP BY and split
        # each model into one row per reference month.
        .order_by()
    )
```

- [ ] **Step 4: Rode os testes e confirme que passam**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test web.tests.test_queries
```

Esperado: OK.

- [ ] **Step 5: Commit**

```bash
git add web/queries.py web/tests/test_queries.py
git commit -m "feat: Filtro de preço sobre as cotações do mês vigente"
```

---

## Task 5: Filtro de marca

**Files:**
- Modify: `web/queries.py`
- Test: `web/tests/test_queries.py`

- [ ] **Step 1: Escreva os testes que falham**

Em `web/tests/test_queries.py`, acrescente à classe `SearchModelsTests`:

```python
    def test_brand_filter_keeps_only_that_brand(self):
        other = build_vehicle(brand_code=13, model_code=9, year=2015, brand_name="Citroën")
        add_quote(other, 2026, 8, "50000.00")
        page = queries.search_models(SearchFilters(brand="1-13"))
        self.assertEqual({c["vehicle_model"].fipe_code for c in page}, {9})

    def test_a_malformed_brand_code_disables_the_filter(self):
        # Mesmo comportamento de um ano não-numérico: desliga o filtro em vez de
        # estreitar para nada por acidente.
        page = queries.search_models(SearchFilters(brand="lixo"))
        self.assertEqual({c["vehicle_model"].fipe_code for c in page}, {1, 2})
```

- [ ] **Step 2: Rode os testes e confirme que falham**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test web.tests.test_queries.SearchModelsTests
```

Esperado: FAIL em `test_brand_filter_keeps_only_that_brand`, que devolve `{1, 2, 9}`.

- [ ] **Step 3: Implemente**

Em `web/queries.py`, dentro de `search_models`, logo depois de `model_years_qs = ModelYear.objects.all()` e antes do filtro de combustível:

```python
    model_years_qs = ModelYear.objects.all()
    if filters.brand:
        brand_lookups = codes.decode_brand(filters.brand)
        # A malformed code disables the filter instead of narrowing to nothing.
        if brand_lookups is not None:
            model_years_qs = model_years_qs.filter(
                **{
                    f"vehicle_model__brand__{field}": value
                    for field, value in brand_lookups.items()
                }
            )
    if filters.fuels:
        model_years_qs = model_years_qs.filter(fuel_type__in=filters.fuels)
```

`codes` já está importado no módulo.

- [ ] **Step 4: Rode os testes e confirme que passam**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test web.tests.test_queries
```

Esperado: OK.

- [ ] **Step 5: Commit**

```bash
git add web/queries.py web/tests/test_queries.py
git commit -m "feat: Filtro de marca pelo código tipo-marca"
```

---

## Task 6: Ordenação por preço

**Files:**
- Modify: `web/queries.py`
- Test: `web/tests/test_queries.py`

- [ ] **Step 1: Escreva o teste que falha**

Em `web/tests/test_queries.py`, acrescente à classe `SearchModelsTests`. As quatro versões ficam sob a marca 13 para não se misturarem com as do `setUp`:

```python
    def test_price_sorting_reads_each_end_of_the_range(self):
        # 28k–90k contra 30k–35k é o único par que denuncia a chave errada:
        # por min_value o primeiro vem antes, por max_value também — mas se as
        # direções trocarem de chave, a ordem inverte.
        for year, value in [(2015, "28000.00"), (2016, "90000.00")]:
            wide = build_vehicle(brand_code=13, model_code=7, year=year, brand_name="Citroën")
            add_quote(wide, 2026, 8, value)
        for year, value in [(2015, "30000.00"), (2016, "35000.00")]:
            narrow = build_vehicle(brand_code=13, model_code=8, year=year, brand_name="Citroën")
            add_quote(narrow, 2026, 8, value)

        def codes_for(sort):
            page = queries.search_models(SearchFilters(brand="1-13", sort=sort))
            return [card["vehicle_model"].fipe_code for card in page]

        # 28.000 antes de 30.000; por max_value daria [8, 7].
        self.assertEqual(codes_for("price_asc"), [7, 8])
        # 90.000 antes de 35.000; por min_value daria [8, 7].
        self.assertEqual(codes_for("price_desc"), [7, 8])
```

- [ ] **Step 2: Rode o teste e confirme que falha**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test web.tests.test_queries.SearchModelsTests.test_price_sorting_reads_each_end_of_the_range
```

Esperado: FAIL — sem ordenação por preço a ordem sai por nome de marca e modelo.

- [ ] **Step 3: Implemente**

Em `web/queries.py`, acrescente a constante logo depois de `PER_PAGE = 24`:

```python
# Ordering by an annotation is safe: unlike a model field, it never joins the
# GROUP BY. Each direction reads the end of the range the reader is looking at.
# The model id breaks ties so two identical queries cannot come back in
# different orders and make the paginator repeat one card and hide another.
SORT_ORDERS = {
    "price_asc": ["min_value", "model_year__vehicle_model"],
    "price_desc": ["-max_value", "model_year__vehicle_model"],
}
```

E troque o bloco que escolhe a ordenação e pagina, em `search_models`:

```python
    if filters.sort:
        # Asked for price, not for relevance: the sort replaces the ranking.
        page = Paginator(rows.order_by(*SORT_ORDERS[filters.sort]), PER_PAGE).get_page(
            filters.page
        )
    elif ranked_ids is None:
        rows = rows.order_by(
            "model_year__vehicle_model__brand__name", "model_year__vehicle_model__name"
        )
        page = Paginator(rows, PER_PAGE).get_page(filters.page)
    else:
        position = {pk: index for index, pk in enumerate(ranked_ids)}
        ordered = sorted(rows, key=lambda row: position[row["model_year__vehicle_model"]])
        page = Paginator(ordered, PER_PAGE).get_page(filters.page)
```

- [ ] **Step 4: Rode os testes e confirme que passam**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test web.tests.test_queries
```

Esperado: OK.

- [ ] **Step 5: Commit**

```bash
git add web/queries.py web/tests/test_queries.py
git commit -m "feat: Ordenação por preço, substituindo a relevância quando ativa"
```

---

## Task 7: Os três controles na tela

**Files:**
- Modify: `web/views.py`, `web/templates/web/home.html`
- Test: `web/tests/test_views.py`

- [ ] **Step 1: Escreva os testes que falham**

Em `web/tests/test_views.py`, acrescente à classe `SearchScreenTests`:

```python
    def test_the_sidebar_offers_the_new_controls(self):
        response = self.client.get(reverse("web:home"))
        for field in ['name="brand"', 'name="price_op"', 'name="price"', 'name="sort"']:
            with self.subTest(field=field):
                self.assertContains(response, field)

    def test_a_price_outside_the_steps_is_offered_back(self):
        # Sem isso a URL compartilhada mostraria um filtro diferente do que pede.
        response = self.client.get(reverse("web:home"), {"price": "43500"})
        self.assertContains(response, "R$ 43.500")

    def test_the_round_steps_are_labelled_short(self):
        response = self.client.get(reverse("web:home"))
        self.assertContains(response, "R$ 50 mil")
```

- [ ] **Step 2: Rode os testes e confirme que falham**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test web.tests.test_views.SearchScreenTests
```

Esperado: FAIL com "Couldn't find 'name=\"brand\"' in response".

- [ ] **Step 3: Implemente a view**

Em `web/views.py`, acrescente o import das constantes de preço:

```python
from web.filters import PRICE_STEPS
from web.filters import SearchFilters
```

Acrescente as listas de rótulos ao lado de `YEAR_OPS`:

```python
YEAR_OPS = [("gte", "a partir de"), ("eq", "exatamente"), ("lte", "até")]
# No "exatamente" here: with fixed steps it would match only the exact amount.
PRICE_OPS = [("gte", "a partir de"), ("lte", "até")]
SORT_OPTIONS = [
    ("", "relevância"),
    ("price_asc", "menor preço"),
    ("price_desc", "maior preço"),
]
FUEL_LABELS = dict(FuelType.choices)


def _price_label(value):
    """R$ 50 mil — the full "R$ 50.000,00" only clutters a narrow select. A value
    that came from a hand-written URL keeps its digits, so it is never rounded
    into a lie."""
    if value % 1000 == 0:
        return f"R$ {value // 1000} mil"
    return f"R$ {value:,}".replace(",", ".")


def _price_steps(current):
    """The fixed steps, plus whatever arrived in the URL if it is not one of
    them: a shared link has to come back showing what it was sharing."""
    steps = list(PRICE_STEPS)
    if current is not None and current not in steps:
        steps.append(current)
    return [(value, _price_label(value)) for value in sorted(steps)]
```

E acrescente as quatro chaves ao dicionário devolvido por `_search_context`, logo depois de `"years"`:

```python
        "years": queries.available_years(),
        "brands": [
            (codes.encode_brand(brand), brand.name) for brand in queries.available_brands()
        ],
        "year_ops": YEAR_OPS,
        "price_ops": PRICE_OPS,
        "price_steps": _price_steps(filters.price),
        "sort_options": SORT_OPTIONS,
```

- [ ] **Step 4: Implemente o template**

Em `web/templates/web/home.html`, dentro do `<aside id="filters">`, acrescente o bloco da marca **antes** do fieldset de combustível:

```html
      <aside id="filters" class="space-y-6">
        <fieldset>
          <legend class="text-sm font-medium text-slate-700">Marca</legend>
          <select name="brand"
                  class="mt-2 w-full rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm">
            <option value="">todas</option>
            {% for value, label in brands %}
              <option value="{{ value }}" {% if filters.brand == value %}selected{% endif %}>{{ label }}</option>
            {% endfor %}
          </select>
        </fieldset>

        <fieldset>
          <legend class="text-sm font-medium text-slate-700">Combustível</legend>
```

E acrescente os blocos de preço e ordenação **depois** do fieldset de ano, antes do `</aside>`:

```html
        <fieldset>
          <legend class="text-sm font-medium text-slate-700">Preço</legend>
          <div class="mt-2 flex gap-2">
            <select name="price_op"
                    class="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm">
              {% for value, label in price_ops %}
                <option value="{{ value }}" {% if filters.price_op == value %}selected{% endif %}>{{ label }}</option>
              {% endfor %}
            </select>
            <select name="price"
                    class="w-full rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm">
              <option value="">qualquer</option>
              {% for value, label in price_steps %}
                <option value="{{ value }}" {% if filters.price == value %}selected{% endif %}>{{ label }}</option>
              {% endfor %}
            </select>
          </div>
        </fieldset>

        <fieldset>
          <legend class="text-sm font-medium text-slate-700">Ordenar por</legend>
          <select name="sort"
                  class="mt-2 w-full rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm">
            {% for value, label in sort_options %}
              <option value="{{ value }}" {% if filters.sort == value %}selected{% endif %}>{{ label }}</option>
            {% endfor %}
          </select>
        </fieldset>
      </aside>
```

Nada de JS novo: os três estão dentro de `#filters`, e o formulário já tem `hx-trigger="submit, change from:#filters"`.

- [ ] **Step 5: Rode a suíte inteira e confirme que passa**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py test
```

Esperado: OK. Se `psycopg` já estiver instalado e o container de pé, rode também sem o override.

- [ ] **Step 6: Confira na tela**

```bash
DATABASE_URL="sqlite:///$PWD/db.sqlite3" ./venv/bin/python manage.py runserver
```

Abra `http://127.0.0.1:8000/`, escolha marca Fiat, preço "até R$ 50 mil" e "menor preço", e confirme que a URL na barra de endereço traz `brand`, `price_op`, `price` e `sort`, e que abrir essa URL numa aba nova reproduz os mesmos controles marcados.

- [ ] **Step 7: Commit**

```bash
git add web/views.py web/templates/web/home.html web/tests/test_views.py
git commit -m "feat: Controles de marca, preço e ordenação na barra lateral"
```

---

## Task 8: Documentar no CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Atualize a lista de telas**

Na seção `## Web`, troque a primeira linha da lista por:

```markdown
- Busca full-text (`/?q=corsa`), com filtros de marca, combustível, ano e preço ao lado, ordenação
  por preço e cards embaixo.
```

- [ ] **Step 2: Documente o código de marca**

Na seção `### Códigos na URL`, troque a lista de formatos por:

```markdown
Três formatos, distinguidos pela contagem de partes — é isso que impede um resolver como o
outro:

- versão (5 partes): `tipo-marca-modelo-ano-combustível` → `1-21-4712-2017-5`
- modelo (3 partes): `tipo-marca-modelo` → `1-21-4712`
- marca (2 partes): `tipo-marca` → `1-21`
```

- [ ] **Step 3: Documente a semântica do preço e da ordenação**

Na seção `### Filtros e cards`, acrescente ao fim, antes de `A página do modelo **ignora os filtros da busca**`:

```markdown
**Preço estreita as cotações, não os anos/modelo**, porque `value` mora em `PriceQuote`. Um
modelo aparece se tiver **ao menos uma versão** na faixa, e a faixa e a contagem do card passam a
descrever só as versões que casaram — igual ao que combustível e ano já fazem. Então "até 30 mil"
pode mostrar `R$ 28.000 – R$ 30.000` de um modelo que também tem uma versão de 90 mil, e isso é o
comportamento certo para "o que cabe no meu orçamento".

O filtro de preço não tem "exatamente", ao contrário do de ano: com degraus fixos ele casaria só
com o valor cravado e pareceria defeito. O default do operador também difere — ano é "a partir
de", preço é "até", porque preço se procura por teto de orçamento.

Um valor de preço fora dos degraus, digitado na URL, **é honrado e devolvido como opção extra do
select**. Descartá-lo quebraria a promessa de que os filtros voltam iguais ao abrir a URL numa
aba nova.

**A ordenação por preço substitui a relevância** enquanto estiver ativa: quem escolheu "menor
preço" pediu preço. Crescente lê `min_value`, decrescente lê `-max_value` — cada direção pela
ponta da faixa que a pessoa está olhando —, e as duas desempatam pelo id do modelo, senão empate
de preço faz o paginador repetir um card e esconder outro.

`available_brands()` é mais estrita que `available_fuels()` e `available_years()`: só oferece
marca que tem cotação no mês vigente. Entre 7 combustíveis uma opção morta passa despercebida;
entre ~100 marcas ela vira uma lista de decepções.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: Filtros de marca e preço, e a ordenação por preço"
```

---

## Fora do escopo deste plano

Registrado no spec e deliberadamente não feito aqui:

- Toggle 0 km / usados — sobrepõe o filtro de ano; unificar os dois é redesenho
- Filtro "só com histórico" — zera o resultado com um único mês no banco
- Marca com múltipla escolha — não cabe em checkboxes numa barra lateral estreita
- `available_years()` e `available_fuels()` continuam podendo oferecer opção que não devolve nada
