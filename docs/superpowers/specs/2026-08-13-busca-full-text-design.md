# Busca full-text com filtros

Data: 2026-08-13
Status: aprovado, pronto para plano de implementação

## Problema

A tela de consulta usa selects em cascata (marca → modelo → ano). Para achar um Corsa é
preciso saber que ele é GM e caçá-lo numa lista de 23 modelos. Queremos digitar "corsa".

Substituir a cascata por uma barra de pesquisa full-text, com filtros ao lado (combustível e
ano) e os modelos encontrados em cards.

## Decisões tomadas

| Questão | Decisão |
|---|---|
| O que é um card | Um card por `VehicleModel`, com faixa de preço entre as versões que passaram no filtro |
| 0 km (ano 32000) | Vale como o ano mais novo possível: `>=` inclui, `<=` e `=` excluem |
| Opções de combustível | Derivadas do banco; e nomear 6=Híbrido e 7=Tetrafuel no enum |
| Navegação | Card → `/modelo/?m=<code>` com os anos; a cascata é removida |
| Motor de busca | FTS5 do SQLite, isolado atrás de `web/search.py` |

### Por que FTS5 e não `icontains`

O CLAUDE.md promete migrar para Postgres sem refatorar, e FTS no SQLite não se parece com FTS
no Postgres. As opções eram: (a) FTS5 de verdade, (b) coluna normalizada com `icontains` — que
roda igual nos dois bancos mas não tem ranking nem é full-text, (c) dois backends mantidos lado
a lado desde já.

Escolhida (a) com o isolamento de (c) mas sem a segunda implementação: todo o SQL específico do
SQLite vive em `web/search.py`, atrás de uma função. Trocar para Postgres é reescrever esse
módulo e uma migração — views, templates e testes de comportamento continuam de pé. Construir
hoje um backend Postgres que ninguém executa seria manter e testar código morto.

## Arquitetura

Cada módulo com uma porta só:

| módulo | responsabilidade | depende de |
|---|---|---|
| `web/search.py` | **único lugar com SQL cru**: termo → ids de `VehicleModel` ranqueados | FTS5 |
| `web/filters.py` | querystring ↔ `SearchFilters`; valida e normaliza | nada |
| `web/queries.py` | aplica filtros, agrega por modelo, histórico/variações | ORM |
| `web/codes.py` | código de modelo (3 partes) e de versão (5 partes) | ORM |

A view nunca vê `MATCH` nem `bm25`. `filters.py` não importa models, então é testável sem banco.

### O índice

Tabela FTS5 autônoma, com o rowid igual ao id do `VehicleModel`:

```sql
CREATE VIRTUAL TABLE vehicle_model_fts USING fts5(
    name, brand,
    tokenize="unicode61 remove_diacritics 2"
);
```

Autônoma e **não** `content='crawler_vehiclemodel'` porque `brand` não é coluna daquela tabela,
é FK: com external content o FTS5 tentaria `SELECT name, brand FROM crawler_vehiclemodel` e
quebraria. O preço é uma cópia do texto — 262 linhas hoje, ~40 mil no catálogo completo.

Ranking `bm25(vehicle_model_fts, 10.0, 1.0)`: o nome pesa dez vezes mais que a marca, para
"fiat" não afogar a busca em 194 modelos. bm25 devolve número negativo (mais negativo = melhor),
então a ordenação é ascendente.

### Sincronismo por triggers

Quatro triggers, criados na mesma migração que a tabela, com `reverse_sql` que derruba tudo:

1. `AFTER INSERT ON crawler_vehiclemodel`
2. `AFTER UPDATE ON crawler_vehiclemodel` (delete + insert)
3. `AFTER DELETE ON crawler_vehiclemodel`
4. `AFTER UPDATE ON crawler_brand` — reescreve as linhas daquela marca

O quarto existe porque `sync.py` faz `update_or_create` na marca justamente para corrigir
rename da FIPE (`crawler/services/sync.py:145`); sem ele o índice guardaria o nome antigo.

Triggers em vez de signals do Django porque valem para qualquer escrita: o `sync.py` hoje usa
`get_or_create`, mas um `bulk_create` futuro não dispararia signal e dispara trigger.

A migração também faz o backfill das linhas existentes.

## Busca

### Termo → sintaxe FTS5

Cada token vira string literal entre aspas, com `*` de prefixo, unidos por `AND`:

```
corsa sedan   ->   "corsa"* AND "sedan"*
```

As aspas são o que neutraliza a entrada: `AND`, `-`, `*`, `(` digitados pelo usuário viram
texto comum em vez de operador, e `MATCH` não estoura. Tokens de 1 caractere são descartados
**quando há outro token maior** — o "1.0" de "gol 1.0" é tokenizado como `1` e `0` e só
adicionaria ruído; quem buscar apenas "c" continua sendo atendido.

Termo que sanitiza para vazio é tratado como busca sem termo, não como erro.

Acento não precisa de tratamento em Python: o mesmo tokenizer `remove_diacritics 2` processa
índice e consulta, então "citroen" acha "Citroën".

### Filtros

- **Combustível**: checkboxes, múltiplos marcados = OR (`fuel_type__in`). As opções saem dos
  `ModelYear` existentes, não de uma lista fixa — código novo da FIPE aparece como número cru
  em vez de sumir do filtro.
- **Ano**: um `select` de operador (`>=`, `=`, `<=`) mais um `select` dos anos existentes,
  decrescente, sem o 32000.

O 0 km sai de graça, **sem código especial**: `year__gte=2015` inclui 32000, `year__lte` e
`year=` excluem. A semântica escolhida é literalmente a da comparação numérica.

### Do filtro ao card

Os filtros agem em `ModelYear`; o card é um `VehicleModel`. A ordem:

1. `ModelYear` filtrado por combustível e ano
2. havendo termo, restringe aos ids que o FTS devolveu
3. junta com `PriceQuote` **só da referência mais recente** — faixa de preço misturando meses
   diferentes seria mentira
4. agrupa por modelo: `Min(value)`, `Max(value)`, `Count(model_year, distinct=True)`
5. ordena por relevância se há termo, senão por marca e nome

O card mostra marca, nome do modelo, a faixa (`R$ 21.500 – R$ 34.900`, ou o valor único quando
só uma versão bate), **quantas versões bateram** e o mês de referência.

"Versões" e não "anos" porque a contagem é de `ModelYear`, que é ano *e* combustível: um modelo
com 2017 flex e 2017 gasolina tem duas versões num ano só. Dizer "2 anos" ali seria errado.

### Ordenação e paginação

São dois caminhos, porque o rank do FTS não existe no banco relacional:

- **Com termo**: `search.py` devolve os ids já ordenados por `bm25`. A agregação é feita sobre
  esses ids e reordenada em Python pela posição na lista do FTS, e o `Paginator` recebe essa
  lista. É aceitável porque a lista de acertos de um termo é pequena por natureza.
- **Sem termo**: não há rank. A ordenação (`brand__name`, `name`) e a paginação acontecem no
  banco, sobre o queryset — que é o caso em que a lista pode ser o catálogo inteiro.

Paginação com o `Paginator` do Django, 24 por página.

## Telas

| URL | o que é |
|---|---|
| `/?q=&fuel=&year_op=&year=&page=` | busca: barra, filtros ao lado, cards embaixo |
| `/modelo/?m=1-21-4712` | **todas** as versões do modelo, com "ver histórico" e "+ comparar" |
| `/veiculo/?v=1-21-4712-2017-5` | inalterada |
| `/comparar/?v=code1,code2` | inalterada, menos a cascata |

A página do modelo **ignora os filtros da busca** e lista todas as versões daquele modelo. Ela
é uma página sobre o modelo, com URL própria e compartilhável — se herdasse o filtro, o mesmo
link mostraria conteúdos diferentes conforme a busca que levou até ele. O caminho de volta fica
no botão "voltar à busca", que preserva a querystring anterior.

O formulário de busca é um GET comum; o HTMX faz `hx-get` com `hx-target="#results"` e
`hx-push-url="true"`, trocando só a lista e mantendo o link copiável. Sem JS, o mesmo
formulário funciona com recarga de página.

O "+ comparar" carrega a seleção atual na querystring, como já faz hoje.

### O que é removido

- `web/templates/web/partials/search_form.html`, `cascade_tail.html`, `year_select.html`
- as views `model_options` e `year_options`, e suas URLs
- o parâmetro `field` e a constante `FIELD_NAMES`
- `queries.brands()` e `queries.vehicle_models()`
- os testes `test_model_fragment_carries_the_target_field`,
  `test_year_fragment_offers_the_shareable_code`,
  `test_unknown_field_falls_back_to_the_search`

## Erros

Nenhuma entrada torta devolve 500. `filters.py` normaliza e descarta: operador fora de
`{gte, eq, lte}` ou ano não-numérico desligam o filtro de ano; `fuel` não-numérico é ignorado;
`page` usa `Paginator.get_page()`, que já trata lixo e página além do fim.

Zero resultados mostra um estado vazio que nomeia os filtros ativos e oferece "limpar filtros".
Banco sem cotação mantém a mensagem de rodar o crawler. `/modelo/?m=` inválido segue o padrão
do `/veiculo/`: 404 de volta para a busca.

## Testes

- **`search.py`** — "citroen" acha "Citroën"; "cors" acha "Corsa" por prefixo; dois tokens são
  AND; relevância põe nome antes de marca. E a entrada hostil: `"`, `AND`, `*`, `-`, `((`, e
  string só de pontuação — nenhuma pode estourar `MATCH`.
- **Triggers, exercitados pelo ORM** (sem SQL no teste) — criar modelo torna ele achável;
  renomear modelo passa a achar pelo nome novo e não pelo velho; renomear marca idem; deletar
  tira do índice.
- **`filters.py`** — sem tocar no banco: operador inválido, ano lixo, fuel repetido, round-trip
  querystring.
- **`queries.py`** — a faixa de preço usa só o mês vigente (montar dois meses e provar que o
  antigo não entra na faixa); a contagem de versões respeita o filtro; 0 km nos três
  operadores; a ordem por relevância sobrevive à agregação.
- **Views** — cards, filtros preservados no link, paginação, estado vazio, e o fragmento HTMX
  devolvendo só a lista.

## Mudança no crawler

A única coisa que toca `crawler/`: acrescentar `HYBRID = 6, "Híbrido"` e
`TETRAFUEL = 7, "Tetrafuel"` ao enum `FuelType`, mais `"hibrido": 6` e `"tetrafuel": 7` em
`FUEL_BY_LABEL` (`crawler/fipe/parsers.py:26`). Custa uma migração de `choices` e um teste no
`parsers`.

É o remédio que o próprio CLAUDE.md prescreve para código desconhecido, e a dívida já está no
banco: 43 registros com combustível 6 (todos híbridos, `32000-6`) e 4 com o 7 (o Grand Siena
TETRAFUEL).

## Fora de escopo

- Ranking de altas e quedas do mês (já pendente antes desta mudança)
- Backend Postgres da busca
- Busca por faixa de preço
- Autocompletar / sugestões enquanto digita
