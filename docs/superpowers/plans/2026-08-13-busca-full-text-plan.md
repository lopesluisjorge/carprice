# Plano: busca full-text com filtros

Design: [`../specs/2026-08-13-busca-full-text-design.md`](../specs/2026-08-13-busca-full-text-design.md)

Seis fases. Cada uma termina com a suíte verde e a aplicação de pé — a cascata atual só é
removida na fase 5, quando a tela nova já existe para substituí-la.

---

## Fase 0 — Nomear os combustíveis 6 e 7

A única mudança em `crawler/`. Feita primeiro porque o filtro de combustível depende dela.

1. `crawler/models.py` — acrescentar ao `FuelType`:
   `HYBRID = 6, "Híbrido"` e `TETRAFUEL = 7, "Tetrafuel"`.
2. `crawler/fipe/parsers.py:26` — acrescentar `"hibrido": 6` e `"tetrafuel": 7` ao
   `FUEL_BY_LABEL`. Conferir que nenhuma chave nova é substring de outra: `_fuel_from_label`
   devolve o primeiro casamento, então ordem importa.
3. `python manage.py makemigrations crawler` — migração só de `choices`, em
   `ModelYear.fuel_type` e `PriceQuote.fuel_type`.
4. Teste em `crawler/tests/test_parsers.py`: rótulo "Híbrido" e "Tetrafuel" viram 6 e 7.

**Verificação:** `manage.py test crawler` verde; no shell, `get_fuel_type_display()` de um
registro com `fuel_type=6` devolve "Híbrido" em vez do número cru.

---

## Fase 1 — Índice FTS5 e `web/search.py`

1. **`web/migrations/0001_vehicle_model_fts.py`**, com `dependencies` na migração da fase 0.
   Um `RunSQL` com `reverse_sql` que derruba tudo:
   - `CREATE VIRTUAL TABLE vehicle_model_fts USING fts5(name, brand, tokenize="unicode61 remove_diacritics 2")`
   - quatro triggers: insert/update/delete em `crawler_vehiclemodel` e update em
     `crawler_brand` (este reescreve as linhas da marca — ver o porquê na spec)
   - backfill: `INSERT INTO vehicle_model_fts(rowid, name, brand) SELECT vm.id, vm.name, b.name FROM crawler_vehiclemodel vm JOIN crawler_brand b ON b.id = vm.brand_id`

   A tabela nasce numa migração, e não num setup de teste, justamente para que
   `manage.py test` a crie no banco de teste como qualquer outro schema.

2. **`web/search.py`** — duas funções:
   - `build_match_query(term)`: tokeniza, descarta token de 1 caractere quando há outro maior,
     envolve cada um em aspas duplas com `*` de prefixo, junta com ` AND `. Devolve `""` se
     nada sobrar.
   - `search(term) -> list[int]`: roda o `MATCH` com `ORDER BY bm25(vehicle_model_fts, 10.0, 1.0)`
     e devolve ids de `VehicleModel` já ordenados. Termo vazio devolve `None` (≠ lista vazia:
     `None` é "sem termo", `[]` é "buscou e não achou").

3. **Reorganizar os testes**: `web/tests.py` vira o pacote `web/tests/`, espelhando
   `crawler/tests/`, com `test_search.py` já dentro e o conteúdo atual dividido depois.

4. Testes de `search.py`: "citroen" acha "Citroën"; "cors" acha "Corsa"; dois tokens são AND;
   nome ranqueia antes de marca; e a entrada hostil — `"`, `AND`, `*`, `-`, `((`, string só de
   pontuação — nenhuma pode estourar o `MATCH`.

5. Testes dos triggers, exercitados **pelo ORM**: criar modelo torna achável; renomear modelo
   passa a achar pelo nome novo e não pelo velho; renomear marca idem; deletar tira do índice.

**Verificação:** `manage.py test web` verde. `migrate` e depois `migrate web zero` devem ir e
voltar sem erro — é o que prova o `reverse_sql`.

---

## Fase 2 — `web/filters.py`

Não importa models, então testa sem banco.

1. Dataclass `SearchFilters`: `term`, `fuels: list[int]`, `year_op`, `year`, `page`.
2. `from_request(request.GET)`: normaliza e descarta lixo — operador fora de
   `{gte, eq, lte}` ou ano não-numérico desligam o filtro de ano; `fuel` não-numérico é
   ignorado; duplicatas somem.
3. `to_querystring(**overrides)`: reconstrói o link preservando o resto — é o que os links de
   paginação e o "limpar filtros" usam.
4. Testes: cada entrada torta acima, mais o round-trip querystring → filtros → querystring.

**Verificação:** `manage.py test web.tests.test_filters` verde, sem banco envolvido.

---

## Fase 3 — Agregação por modelo em `web/queries.py`

1. `latest_reference_table()` — a referência mais recente, usada pela faixa de preço.
2. `search_models(filters)` — o caminho descrito na spec: `ModelYear` filtrado → restrição pelos
   ids do FTS quando há termo → junção com `PriceQuote` **só da referência mais recente** →
   agrupamento com `Min`, `Max`, `Count(distinct)` → ordenação → `Paginator` (24 por página).
   Os dois caminhos de ordenação da spec: com termo reordena em Python pela posição do FTS;
   sem termo ordena e pagina no banco.
3. `available_fuels()` e `available_years()` para a barra lateral, derivados do banco.
4. `model_versions(vehicle_model)` para a página do modelo — **todas** as versões, sem filtro.
5. Remover `brands()` e `vehicle_models()`.
6. Testes: faixa de preço só do mês vigente (montar dois meses e provar que o antigo não entra);
   contagem de versões respeita o filtro; 0 km nos três operadores; a ordem por relevância
   sobrevive à agregação.

**Verificação:** `manage.py test web` verde.

---

## Fase 4 — Código de modelo em `web/codes.py`

1. `encode_model(vehicle_model)` → `1-21-4712`; `decode_model(code)`; `get_model(code)`.
   Mesma disciplina do código de versão: partes numéricas, tipo de veículo na frente.
2. Testes de round-trip e de código malformado (3 partes vs 5 não podem se confundir).

**Verificação:** `manage.py test web.tests.test_codes` verde.

---

## Fase 5 — Views, templates e URLs

A fase que troca a interface. Só aqui a cascata sai.

1. **`web/views.py`**: `home` vira a busca (monta filtros, chama `search_models`, devolve
   página inteira ou só o fragmento quando `HX-Request`); nova view `model_detail` para
   `/modelo/?m=`; apagar `model_options`, `year_options`, `_field`, `FIELD_NAMES`.
2. **`web/urls.py`**: remover as duas rotas de fragmento, acrescentar `/modelo/`.
3. **Templates novos**: `home.html` reescrito (barra de busca no topo, `<aside>` de filtros,
   `#results` embaixo), `partials/results.html` (grade de cards + paginação — é o alvo do
   HTMX), `partials/model_card.html`, `model.html`.
4. **Templates apagados**: `partials/search_form.html`, `partials/cascade_tail.html`,
   `partials/year_select.html`.
5. **`compare.html`**: tirar o include do formulário em cascata; o "+ comparar" agora vem da
   página do modelo. Acrescentar "voltar à busca".
6. Apagar os três testes da cascata nomeados na spec; escrever os das telas novas: cards,
   filtros preservados no link, paginação, estado vazio, e o fragmento HTMX devolvendo só a
   lista.

**Verificação:** `manage.py test` inteiro verde; e no navegador, com o Tailwind reconstruído,
buscar "corsa", filtrar por combustível e por ano, abrir um modelo, mandar duas versões para o
comparador.

---

## Fase 6 — Documentação

1. `CLAUDE.md`: reescrever a seção **Web** (a cascata não existe mais), acrescentar
   `search.py` e `filters.py` à **Estrutura**, e registrar em **Estado atual** que os
   combustíveis 6 e 7 foram nomeados.
2. Documentar as duas armadilhas que custariam meia hora a quem mexer depois: por que a tabela
   FTS5 é autônoma e não `content=`, e por que existe trigger no `crawler_brand`.

**Verificação:** `manage.py test` verde e `manage.py check` limpo.

---

## Riscos conhecidos

- **O Tailwind precisa ser reconstruído** ao fim da fase 5: as classes novas dos cards e da
  barra lateral não estão no `app.css` atual. É o seu comando, não o meu.
- **`ruff` não está no venv**, então o `ruff check .` do CLAUDE.md continua sem rodar. Sigo o
  estilo do `crawler/` na mão.
- **A tabela FTS5 é invisível para o ORM.** Um `loaddata` ou uma escrita em SQL cru que não
  passe pelos triggers deixa índice e tabela fora de sincronia. Os triggers cobrem todo o
  caminho normal (ORM e crawler); se isso virar problema de verdade, o remédio é um comando de
  reconstrução — deliberadamente fora de escopo agora.
