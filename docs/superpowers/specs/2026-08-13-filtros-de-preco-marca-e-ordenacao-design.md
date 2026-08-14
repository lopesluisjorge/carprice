# Filtros de preço e marca, e ordenação por preço

Data: 2026-08-13
Status: aprovado, pronto para plano de implementação

## Problema

A busca tem filtros de combustível e ano, e nada mais. Faltam os dois recortes que qualquer
pessoa procurando carro faz primeiro — **quanto custa** e **de que marca é** — e falta poder
ordenar pelo preço, que é o que torna um filtro de faixa útil de verdade.

Hoje, para ver só Fiat, é preciso digitar "fiat" na busca e torcer para o ranking não afogar o
resultado; e para achar algo até 50 mil não há caminho nenhum.

## Escopo

Entra: filtro de preço (operador + valor), filtro de marca, ordenação por preço.

Não entra, e por quê:

| Ideia | Por que fica de fora |
|---|---|
| Toggle 0 km / usados | Sobrepõe o filtro de ano (0 km é o ano 32000); unificar os dois controles é redesenho, não acréscimo |
| Filtro "só com histórico" | É o filtro mais alinhado ao diferencial do projeto, mas com um único mês no banco ele zera o resultado. Vale quando houver o segundo mês |
| Filtro de tipo de veículo | Só carros são coletados; seria um select de uma opção |
| Marca com múltipla escolha | ~100 marcas não cabem em checkboxes numa barra lateral estreita. Um select resolve o caso comum |

## Decisões tomadas

| Questão | Decisão |
|---|---|
| Entrada do valor | Select de degraus fixos, espelhando o filtro de ano |
| Degraus | 10, 20, 30, 50, 75, 100, 150 e 200 mil |
| Operadores de preço | Só "a partir de" e "até" — sem "exatamente" |
| Default do operador | `lte` ("até"), diferente do ano, que é `gte` |
| Valor fora dos degraus | Honrado, e renderizado como opção extra |
| Identidade da marca na URL | Código de duas partes, `tipo-marca` (`1-21`) |
| Semântica do filtro de preço | Modelo entra se tiver ao menos uma versão na faixa |
| Ordenação por preço | Crescente pelo menor preço, decrescente pelo maior |
| Ordenação x relevância | A ordenação por preço substitui a relevância enquanto estiver ativa |

### Por que sem "exatamente"

O filtro de ano tem três operadores e o de preço herda só dois. Com degraus fixos,
"exatamente R$ 50.000" casaria apenas com quem custa 50.000,00 cravado — quase nada. Seria um
botão que devolve vazio e parece defeito.

### Por que o código da marca tem duas partes

A FIPE numera marcas **por tipo de veículo**: a marca 21 de carro não é a mesma de moto. Um
`?brand=21` funciona hoje, quando só carros são coletados, e passa a casar as duas marcas 21 em
silêncio no dia em que motos entrarem — exatamente a classe de armadilha que o `codes.py` existe
para evitar. O módulo já distingue códigos pela contagem de partes (3 para modelo, 5 para
versão); um de 2 partes entra na mesma convenção e custa duas funções pequenas.

### Por que o default do preço é "até"

Ano se procura "a partir de 2015". Preço se procura por teto de orçamento. A assimetria é
deliberada e é a única diferença de comportamento entre os dois controles.

### Por que valor fora dos degraus é honrado

O `CLAUDE.md` promete que "os filtros voltam iguais ao abrir a URL numa aba nova". Descartar um
`?price=43500` digitado à mão quebraria essa promessa em silêncio. O valor é aceito, e a tela o
renderiza como opção extra do select para que a URL e o que aparece na tela nunca discordem.

## Arquitetura

Nenhum módulo novo. Cada um recebe a parte que já é da sua alçada:

| módulo | o que ganha | por quê |
|---|---|---|
| `web/filters.py` | Os quatro campos, `PRICE_LOOKUPS`, `PRICE_STEPS` | É o dono da tradução querystring ↔ dataclass, e continua sem importar models |
| `web/codes.py` | `encode_brand`, `decode_brand`, `get_brand` | É o dono dos identificadores compartilháveis |
| `web/queries.py` | Aplicação dos filtros, `available_brands()`, `SORT_ORDERS` | É o dono de toda leitura do banco |
| `web/views.py` | Rótulos em português das novas opções | Já é onde moram `YEAR_OPS` e `FUEL_LABELS` |
| `home.html` | Três controles dentro de `#filters` | O `hx-trigger` já é `change from:#filters` |

### `web/filters.py`

```python
brand: str = ""              # código tipo-marca: "1-21"
price_op: str = "lte"        # "gte" | "lte"
price: int | None = None     # em reais
sort: str = ""               # "" | "price_asc" | "price_desc"
```

`PRICE_LOOKUPS = {"gte": "value__gte", "lte": "value__lte"}`, ao lado de `YEAR_LOOKUPS`. A
diferença silenciosa entre os dois: os do ano miram `ModelYear.year` e os de preço miram
`PriceQuote.value` — models diferentes. O módulo segue sem importar models, então são só
strings; quem sabe onde aplicar é o `queries.py`.

`is_empty` e `querystring()` passam a cobrir os quatro campos. `price_op` só entra no link
quando há `price`, mesmo tratamento que `year_op` já recebe.

`PRICE_STEPS` guarda **reais**, não milhares: `[10_000, 20_000, 30_000, 50_000, 75_000,
100_000, 150_000, 200_000]`. Só o rótulo na tela abrevia. `sort` vazio é a ordenação de hoje —
relevância quando há termo, marca e nome quando não há.

`sort` fica aqui apenas como vocabulário validado (valor desconhecido vira `""`). O mapa para
`order_by` mora no `queries.py`, porque ordena por `min_value`/`max_value`, que são anotações
inventadas lá — `filters.py` não pode conhecê-las sem passar a depender de como a consulta é
montada.

### `web/codes.py`

```
marca (2 partes):  tipo-marca            -> 1-21
modelo (3 partes): tipo-marca-modelo     -> 1-21-4712
versão (5 partes): tipo-marca-modelo-ano-combustível -> 1-21-4712-2017-5
```

`decode_brand` devolve lookups relativos a `Brand` (`{"vehicle_type": …, "fipe_code": …}`);
quem chama prefixa conforme o ponto de partida da consulta.

### `web/queries.py`

Marca, ano e combustível estreitam `model_years_qs`. **Preço estreita as cotações**, porque
`value` mora em `PriceQuote`:

```python
quotes = PriceQuote.objects.filter(reference_table=reference, model_year__in=model_years_qs)
if filters.price is not None:
    quotes = quotes.filter(**{filters.price_lookup: filters.price})
```

`available_brands()` devolve só marcas com cotação na referência mais recente. É uma divergência
consciente de `available_fuels()` e `available_years()`, que não filtram por preço: entre 7
combustíveis uma opção morta passa despercebida; entre ~100 marcas ela vira uma lista de
decepções.

`SORT_ORDERS` mapeia `price_asc` para `min_value` e `price_desc` para `-max_value`, os dois com
desempate por `model_year__vehicle_model` — o campo pelo qual as linhas já são agrupadas.
Ordenar por anotação é seguro — anotação não entra no `GROUP BY`,
ao contrário de campo de model, que é a armadilha do `Meta.ordering` já documentada.

## Semântica do filtro de preço

Um modelo aparece se tiver **ao menos uma versão** dentro da faixa. A faixa de preço e a
contagem de versões do card refletem **só as versões que casaram**.

Isso não é invenção deste filtro: combustível e ano já se comportam assim hoje. A consequência
visível é que "até 30 mil" pode mostrar um card `R$ 28.000 – R$ 30.000` de um modelo que também
tem uma versão de 90 mil. É o comportamento correto para "me mostre o que cabe no meu
orçamento".

## Ordenação

Enquanto `sort` estiver preenchido, ele **substitui** a relevância da busca. Quem escolheu
"menor preço" pediu preço, não pertinência ao termo.

O desempate pelo id do modelo não é enfeite: é a mesma lição do bug corrigido na busca em
`463a5a3` — com ranking empatado e sem desempate, duas consultas idênticas podem devolver ordens
diferentes, e o paginador mostra um card duas vezes e outro nenhuma.

## Tela

Três controles novos dentro do `<aside id="filters">`: Marca, Preço (operador + valor) e
Ordenar por. Como o `hx-trigger` já é `change from:#filters`, eles entram funcionando sem JS
novo, e sem JS nenhum a página recarrega igual — a garantia que a busca já dá.

Rótulos dos degraus em português curto ("R$ 50 mil", não "R$ 50.000,00", que polui um select
estreito).

## Testes

| arquivo | o que cobre |
|---|---|
| `test_filters.py` | Round-trip dos quatro campos; operador inválido caindo no default; preço não-numérico virando `None`; `sort` desconhecido virando vazio |
| `test_codes.py` | Round-trip do código de marca e recusa por contagem de partes — 2, 3 e 5 não podem resolver um como o outro |
| `test_queries.py` | Modelo entra por ter uma versão na faixa; a faixa do card encolhe para as versões que casaram; filtro de marca; as duas direções de ordenação |

O caso que distingue as duas direções de ordenação precisa de um modelo 28k–90k contra um
30k–35k: com qualquer outro par, ordenar por `min_value` e por `max_value` dá o mesmo resultado
e o teste passaria com a implementação errada.

Os testes precisam continuar verdes nos dois engines (Postgres e SQLite), como o resto da suíte.

## Pendências que este trabalho não resolve

- **Marca com múltipla escolha.** Um select só permite uma marca por vez.
- **Filtro "só com histórico"**, quando houver um segundo mês coletado.
- **`available_years()` e `available_fuels()` seguem sem filtrar por cotação**, então ainda
  podem oferecer opção que não devolve nada. Só a lista de marcas nasce com esse cuidado.
