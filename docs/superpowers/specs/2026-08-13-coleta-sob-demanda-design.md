# Coleta sob demanda disparada pela busca

Data: 2026-08-13
Status: aprovado, pronto para plano de implementação

## Problema

Hoje o banco tem um único mês (08/2026), então gráfico e variação aparecem vazios. Coletar tudo
não é viável: a API aceita ~40 requisições/min e o catálogo tem 4.439 versões.

A ideia: buscar por um veículo agenda a coleta do histórico daqueles modelos, executada em
background dentro da cota.

## Escala: o número que decidiu o desenho

Simulação do fan-out no banco atual, com o conjunto de meses pedido:

| busca | modelos | versões | requisições | tempo a 40/min |
|---|---|---|---|---|
| `aircross` | 14 | 41 | ~500 | 13 min |
| `siena` | 11 | 48 | ~570 | 14 min |
| `palio` | 100 | 425 | ~9.100 | 3,8 h |
| `gol` | 157 | 689 | ~16.100 | 6,7 h |

Uma busca por "gol" ocuparia o crawler por quase sete horas, e a cota é global — trava qualquer
outra coleta nesse período. Daí o orçamento por passada e a fila com justiça (abaixo).

## Decisões tomadas

| Questão | Decisão |
|---|---|
| Conjunto de meses | mês atual, 3, 6 e 12 meses atrás (o "122" do enunciado era engano) |
| Busca ampla | fila priorizada por relevância, com orçamento por passada; nada se perde |
| "Similaridade muito próxima" | cobertura de modelos, não semelhança textual |
| Feedback na tela | faixa de estado, sem auto-refresh |
| Background | worker próprio (`process_crawl_queue`), não thread na view |

### Por que worker e não thread

A cota é uma janela deslizante em memória, dentro de uma instância de `FipeClient`. Um processo
consumidor = uma janela = 40 req/min de verdade. Qualquer execução dentro do processo web
multiplica isso pelo número de workers: dois workers gunicorn viram 80 req/min e reintroduzem o
429 que acabou de ser corrigido.

Ganha também sobreviver a restart e ser testável sem thread. O custo, que precisa estar dito em
voz alta no CLAUDE.md: **nada coleta sozinho até o worker ser iniciado**.

## Modelo de dados

Duas tabelas em `crawler/models.py` — o crawler é dono do schema:

```
CollectionRequest   term, vehicle_type, status, created_at, started_at, finished_at,
                    models_done, quotes_created, quotes_updated, quotes_missing,
                    requests_spent, last_error
                    vehicle_models = M2M -> VehicleModel, through=CollectionItem

CollectionItem      request FK, vehicle_model FK, rank, status, finished_at
                    unique(request, vehicle_model)
```

`status` de `CollectionRequest`: `pending`, `running`, `partial`, `completed`, `failed`.
`status` de `CollectionItem`: `pending`, `done`.

`CollectionItem` **é** a tabela through do M2M — não existem duas listas de modelos por pedido.
`models_total` também não existe como campo: é `items.count()`, e um contador redundante só
criaria a chance de divergir.

**Um item por modelo, não por par (versão, mês).** Materializar os pares no agendamento
significaria escrever 16 mil linhas dentro do ciclo request/response de "gol". O item guarda o
modelo e o `rank` do FTS; o worker expande cada modelo em pares quando chega a vez dele.

**Sem FK para `ReferenceTable` no agendamento.** Só a FIPE sabe quais tabelas existem, e as de
meses passados podem não estar no banco. O worker resolve `(ano, mês)` para uma `ReferenceTable`
real quando roda, criando a linha se necessário e pulando o período se a FIPE não o tiver.

## Fronteira

`web` continua sem tocar na FIPE: a view grava a intenção via `crawler/services/scheduling.py`,
que só usa o ORM. Quem fala com a FIPE é o worker.

A regra do CLAUDE.md ganha uma formulação mais precisa: **`web` escreve pedido, nunca executa
coleta.** Um teste segura essa porta (ver Testes).

## Expansão de períodos

Função pura em `crawler/services/scheduling.py`, sem banco:

```
periods_for(ano_da_versão, hoje) -> [(ano, mês), …]   # mais recente primeiro
```

Para hoje = 08/2026 e uma versão 2015: `2026-08`, `2026-05`, `2026-02`, `2025-08`, e o passo
anual `2024-08` … `2016-08` — 13 períodos. O ponto de 12 meses coincide com o primeiro passo
anual; o conjunto elimina a duplicata.

Duas regras que o enunciado não fixava:

- **O piso é por versão, não por modelo.** O passo anual desce até `ano_da_versão + 1`. Uma
  versão 2020 não tem preço na tabela de 2005: perguntar é 404 garantido e custa um slot da
  cota. É a diferença entre 9.100 e 13.600 requisições no caso "palio".
- **0 km (ano 32000) não tem passo anual** — só mês atual, 3, 6 e 12. É carro do ano corrente.

## Dedup por cobertura

Busca por T resolve para o conjunto de modelos S:

1. Reúne os modelos cobertos por agendamentos criados nas últimas 48h, **em qualquer status**,
   inclusive pendentes — senão uma fila lenta geraria agendamentos duplicados.
2. `faltantes = S − cobertos`.
3. Vazio → não cria nada; a tela mostra o agendamento existente que cobre a busca.
4. Não vazio → cria um agendamento só com os faltantes, preservando a ordem de relevância.

Mais estrito que similaridade textual e sem limiar arbitrário: `palio fire` depois de `palio`
não reagenda nada, mas uma busca genuinamente mais ampla agenda só a parte nova.

Duas buscas simultâneas pelo mesmo termo podem criar dois agendamentos, porque a checagem e a
escrita não são atômicas. É inofensivo: o pulo de par já coletado impede trabalho duplicado, e
o segundo agendamento termina imediatamente.

## Worker

`manage.py process_crawl_queue`, com `--once` (padrão), `--forever --interval`, `--budget`
(padrão 1.500), `--requests-per-minute` e `--dry-run`.

Cada passada:

1. Toma o lock de arquivo. Ocupado → sai avisando.
2. Devolve a `partial` qualquer pedido que ficou `running` — é resto de um worker que morreu, e
   sem isso ele nunca mais seria escolhido.
3. Resolve as tabelas de referência da FIPE **uma vez** por passada.
4. Para cada pedido com itens pendentes, do mais antigo para o mais novo: percorre os itens na
   ordem de `rank`, gastando no máximo `--budget` requisições **naquele pedido**.
5. Orçamento esgotado → marca o pedido `partial` e vai para o **próximo pedido**. Itens
   pendentes acabaram → marca `completed`. A passada termina quando todos os pedidos foram
   visitados uma vez.

**O orçamento é por pedido dentro de uma passada.** O worker gasta até `--budget` num pedido e
passa adiante, em vez de drenar o primeiro até o fim; na passada seguinte volta de onde parou.
É o que impede "gol" de monopolizar a fila sem que nada se perca. Com `--once` a passada é
limitada a (nº de pedidos × orçamento); com `--forever` o ciclo se repete a cada `--interval`.

**Dentro de cada modelo, a varredura é por período, não por versão** — o mês atual de todas as
versões primeiro, depois 3 meses, depois 6. Um orçamento esgotado deixa um retrato completo do
mês corrente em vez de histórico completo de duas versões e nada das outras trinta.

**Par já coletado é pulado**: antes de pedir, checa se existe `PriceQuote` para
`(model_year, reference_table)`. É o que torna a segunda busca quase gratuita e a operação
idempotente.

### Lock

`fcntl.flock` (stdlib, não-bloqueante) sobre `.crawl_queue.lock` em `BASE_DIR`. Um segundo
worker sai avisando em vez de duplicar a janela de cota — que é a razão de o worker existir. O
SO libera o lock se o processo morrer.

Limitação honesta, a registrar no CLAUDE.md: **vale por máquina, não por cluster.**

## Erros

| situação | tratamento |
|---|---|
| `FipeNotFound` num par | conta em `quotes_missing`, não repete — a FIPE realmente não precifica aquilo |
| período que a FIPE não tem | pula o período; não é erro |
| `FipeRateLimited` / `FipeUnavailable` | grava `last_error`, deixa o pedido `partial` com itens pendentes, sai com código ≠ 0 |
| crash do worker | lock liberado pelo SO; itens pendentes intactos |

## Tela

`web/views.py` chama `scheduling.request_collection(term, model_ids)` apenas quando há termo —
mexer nos filtros de combustível ou ano não dispara nada. O agendamento considera todos os
modelos que o FTS achou, não só a página visível.

A faixa (`web/templates/web/partials/collection_status.html`) mostra o estado do agendamento que
cobre a busca:

- pendente: "coleta de histórico agendada para N modelos"
- parcial: "coletando histórico — X de N modelos"
- concluído: "histórico coletado há Xh"

**A faixa diz "agendada", não "coletando", enquanto o pedido está pendente.** A app web não tem
como saber se o worker está de pé, e afirmar que está seria mentira sempre que ele não estiver.

## Testes

- **`periods_for`** — função pura, sem banco: casos tabelados, 0 km sem passo anual, a duplicata
  de 12 meses colapsando, e a virada de ano (busca em janeiro → 3 meses atrás cai em outubro do
  ano anterior).
- **Dedup** — subconjunto não reagenda; sobreposição parcial agenda só o resto; agendamento com
  mais de 48h deixa de cobrir; pendente conta como coberto.
- **Agendamento** — busca com termo cria o pedido com o `rank` do FTS; busca sem termo não cria;
  segunda busca não cria.
- **Worker** — pula par já coletado; respeita o orçamento, marca `partial` e passa ao pedido
  seguinte em vez de drenar o primeiro; retoma de onde parou na passada seguinte;
  `FipeNotFound` vira `missing`; a ordem é período-major; um pedido deixado em `running` por um
  worker morto volta a ser processado; o lock barra o segundo processo.
- **Fronteira** — um teste que falha se `web/` passar a importar `FipeClient`.

Nenhum teste toca a rede: o worker recebe o cliente por injeção, como o resto do crawler, e os
testes usam `FakeFipeClient`.

## Fora de escopo

- Ranking de altas e quedas do mês (pendente desde antes)
- Auto-refresh dos cards enquanto a coleta roda
- Fila distribuída entre máquinas
- Agendar coleta a partir da página do modelo ou do comparador
