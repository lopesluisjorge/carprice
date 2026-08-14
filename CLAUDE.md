# CarPrice

Aplicação web Django que coleta a tabela FIPE, guarda o histórico mensal de preços e permite
consultar e comparar veículos. O diferencial sobre o site da FIPE é o histórico: o site oficial
mostra apenas o mês vigente, aqui guardamos todos os meses coletados.

## Regras do projeto

- **Nunca instale pacotes.** Liste o que seria necessário instalar e espere a decisão do usuário.
- **Código, schema e identificadores em inglês.** Nomes de models, campos, tabelas, funções,
  variáveis, arquivos e branches: inglês.
- **Texto visível ao usuário em português.** Templates, labels, mensagens de erro, validações de
  formulário e textos do admin: português do Brasil.
- Comentários e docstrings: inglês, e apenas quando o código não se explica sozinho.
- Sem Selenium. A coleta é HTTP puro contra a API JSON da FIPE (decisão tomada no design).
- Sem CDN no front. Todo asset de terceiro é baixado para `web/static/vendor/`.

## Stack

- Django 6.1, Python 3.14 (venv em `./venv`)
- **Três dependências, e só.** `django-environ` (lê `DATABASE_URL`) e `psycopg` (driver do
  Postgres) além do Django. O cliente HTTP continua usando `urllib` da stdlib e os testes, o
  runner do Django. Se `requests` for instalado, a troca se resume a `FipeClient._post`.
- **Postgres em produção e no desenvolvimento local, num container** (`compose.yml`). O
  `DATABASE_URL` decide, e o default das settings já aponta para o container — `docker compose
  up -d` é o setup inteiro.
- **SQLite continua suportado**, não como legado: é o caminho de rodar a suíte sem container
  nenhum (`DATABASE_URL=sqlite:///db.sqlite3`). Os mesmos testes valem nos dois engines, e é
  isso que impede um deles de apodrecer sem ninguém perceber.
- Front: templates Django + HTMX (fragmentos) + Alpine.js (estado leve de UI)
- Tailwind pelo **binário standalone** — sem npm, sem `node_modules`
- Gráficos: ApexCharts
- Testes: `manage.py test` com fixtures gravadas e um cliente falso

## Estrutura

```
carprice/          settings, urls, wsgi/asgi
crawler/           domínio + coleta (dono do schema)
  fipe/client.py     HTTP puro: um método por endpoint, retry/backoff, rate limit
  fipe/parsers.py    JSON bruto -> dataclasses; sem acesso a banco
  services/sync.py   orquestração: percorre a árvore e faz upsert idempotente
  management/commands/crawl_fipe.py   CLI fina: argparse + progresso, nada de lógica
  models.py
  tests/fixtures/    JSONs reais da FIPE usados nos testes
web/               views, templates, static (só lê o banco)
  search.py          ÚNICO SQL cru: termo -> ids de modelo ranqueados por FTS5
  filters.py         querystring <-> SearchFilters; não importa models, testa sem banco
  queries.py         toda leitura do banco: agregação dos cards, histórico, variações
  codes.py           códigos compartilháveis de versão (5 partes) e de modelo (3)
  templatetags/formatting.py   R$ e % em pt-BR, sem depender de locale instalado
  migrations/0001    tabela FTS5 + triggers (o app não tem models próprios)
```

Fronteira que não deve ser cruzada: **`web` nunca chama a FIPE**, só consulta o banco.
`crawler/fipe/` nunca importa models — recebe e devolve dataclasses.

## Modelo de dados

```
ReferenceTable   fipe_code, month, year              # tabela de referência mensal da FIPE
Brand            fipe_code, name, vehicle_type       # CAR=1, MOTORCYCLE=2, TRUCK=3
VehicleModel     brand FK, fipe_code, name
ModelYear        vehicle_model FK, year, fuel_type, fipe_year_code
PriceQuote       model_year FK, reference_table FK, value, fuel_type, fipe_code, collected_at
                 unique(model_year, reference_table)
CrawlRun         reference_table FK, status, started_at, finished_at, counters, last_error
CrawlCheckpoint  crawl_run FK, brand FK, done        # suporte a --resume
```

`PriceQuote` é a tabela grande (uma linha por versão/ano/mês) — mantenha o índice em
`(model_year, reference_table)` e evite queries que a varram sem filtro de `reference_table`.

O combustível fica em dois lugares de propósito: `ModelYear.fuel_type` vem do código FIPE do ano
(`"2026-5"` → Flex) e `PriceQuote.fuel_type` vem do payload de preço — é o que a FIPE de fato
precificou, e permite ler a cotação sozinha. `FuelType`: 1 Gasolina, 2 Álcool, 3 Diesel,
4 Elétrico, 5 Flex. **A FIPE adiciona códigos sem avisar** — um código desconhecido é gravado
como veio, aparecendo como número puro, em vez de virar gasolina silenciosamente. Se aparecer
um número cru na UI, é isso: acrescente o código ao enum e a `FUEL_BY_LABEL`.

`vehicle_type` existe desde o início, mas **só carros são coletados por enquanto**. Motos e
caminhões devem funcionar sem migração: qualquer código novo trata o tipo como parâmetro, nunca
como constante embutida.

## Coleta

Endpoints usados (`https://veiculos.fipe.org.br/api/veiculos/`): `ConsultarTabelaDeReferencia`,
`ConsultarMarcas`, `ConsultarModelos`, `ConsultarAnoModelo`, `ConsultarValorComTodosParametros`.

```bash
python manage.py crawl_fipe                      # mês corrente, só carros
python manage.py crawl_fipe --brands-only        # só o catálogo de marcas (2 requisições)
python manage.py crawl_fipe --models-only --brand 21   # modelos e anos da marca, sem cotações
python manage.py crawl_fipe --models-only --brand 21 --refresh-existing  # reprocessa os já salvos
python manage.py crawl_fipe --reference 2024-01  # backfill de um mês específico
python manage.py crawl_fipe --brand 21 --limit 50
python manage.py crawl_fipe --resume             # retoma o último CrawlRun incompleto
python manage.py crawl_fipe --requests-per-minute 20   # mais conservador que o padrão (40)
python manage.py crawl_fipe --dry-run
```

`--brands-only` é uma atualização de catálogo, não uma coleta de preços: não cria `CrawlRun` nem
checkpoints, de propósito — checkpoints ali fariam um `--resume` posterior pular marcas cujos
preços nunca foram coletados. Por isso ele recusa `--brand`, `--limit` e `--resume`.

`--models-only` desce um nível: atualiza modelos e anos/modelo das marcas indicadas (ou de todas,
sem `--brand`), ainda **sem cotações**. Pela mesma razão do `--brands-only`, não cria `CrawlRun`
nem checkpoints, e recusa `--brands-only`, `--limit` e `--resume`. É o passo para popular a busca
de uma marca sem gastar a cota de cotações.

**Por padrão ele pula os modelos que já estão salvos**, sem gastar requisição. O custo aqui é de
uma requisição por modelo (`ConsultarAnoModelo`), e uma marca grande passa de 500 — a Chevrolet
tem 556, ou ~14 minutos a 40 req/min. Sem o pulo, retomar uma varredura interrompida pagava tudo
de novo desde o começo, em ordem alfabética, e um modelo lá no fim da lista nunca chegava a ser
salvo.

"Já salvo" é **modelo que tem anos/modelo**, não modelo que tem linha: uma varredura interrompida
pode deixar a linha do modelo sem nenhum ano, e tratar isso como pronto abandonaria o modelo para
sempre. `_stored_model_codes` é quem decide, e ela carrega o `.order_by()` vazio obrigatório —
sem ele o `Meta.ordering` do `VehicleModel` arrastaria `name` para o `SELECT` e o `DISTINCT`
passaria a valer para o par.

`--refresh-existing` desliga o pulo e reprocessa tudo. É o que restaura o `update_or_create` em
modelo e ano/modelo — ou seja, **uma renomeação da FIPE só é corrigida (e reindexada no FTS pelos
triggers) com essa flag**. Esse é o preço consciente do padrão: varredura barata de retomar, ao
custo de não reparar nomes sozinha. A flag é recusada sem `--models-only`, onde não faria nada.

Invariantes:

- **Idempotente.** Rodar duas vezes o mesmo mês não duplica nem altera nada.
- **Retomável.** Falha no meio deixa `CrawlRun` + `CrawlCheckpoint` consistentes para `--resume`.
- **Dentro da cota.** No máximo `--requests-per-minute` **requisições** por minuto corrido
  (padrão 40), em **janela deslizante**: cada requisição — de qualquer endpoint — segura um slot
  por exatamente 60s e o devolve no instante em que o minuto fecha, então a coleta entra em ritmo
  constante em vez de parar em blocos. Não aumente sem o usuário pedir — a API é sensível, com
  teto flexível observado em ~50/min.

  A cota é **por requisição, não por cotação** de propósito: a FIPE responde 429 em qualquer
  endpoint, inclusive `ConsultarAnoModelo`. Uma versão anterior contava só as cotações (`price`),
  e o `--models-only` — que nunca chama `price` — passava reto pela cota e tomava 429 em série.
  O slot é tomado no `_post`, então todo endpoint entra na conta; `quotes_requested` continua
  existindo, mas só para o relatório final.

### Limites da API

A API é pública e gratuita (é a que o site da FIPE consome), mas não é documentada nem tem SLA.
Dois comportamentos já observados na prática, que o cliente trata e você não deve "simplificar":

- **403 para User-Agent não-navegador.** Por isso o cliente manda um UA de Chrome.
- **429 com pouquíssimo volume.** `_post` trata 429 como espera, não como erro: dorme
  **exatamente 60s** (ignorando `Retry-After` de propósito — o comportamento observado é de
  janela de 1 minuto), libera todos os slots e repete a mesma requisição, com orçamento próprio
  (`max_rate_limit_retries`, padrão 5). Só depois disso vira `FipeRateLimited`.

O cliente conta `requests_made`, `quotes_requested` e `rate_limited_at` (números das requisições
que levaram 429), e o comando imprime isso no fim. É o que permite descobrir em qual requisição
o limite estoura.

### Log de progresso

Toda linha sai por `Command.log`, que carimba data-hora local (`TIME_ZONE`). Não escreva em
`self.stdout` direto no comando — passe por `log`, senão a linha sai sem horário.

`CrawlProgress` (em `services/sync.py`) mantém a contagem viva de marcas e modelos —
existentes, processados e faltantes. O comando passa a mesma instância para o `on_wait` do
cliente, então **toda pausa imprime onde a varredura está**:

```
[2026-08-12 23:24:47]   Cota de 20 requisições/min atingida na requisição #21 (cotação #10);
  aguardando 53.0s pelo próximo slot. — marca 1/1 (Fiat) · modelos: 585 existentes,
  3 processados, 582 faltantes · 10 cotações
```

Esse acoplamento é intencional: as pausas são os momentos em que se quer saber o progresso, e
evitam poluir o log a cada modelo. Com a janela deslizante as esperas viram de poucos segundos,
então esperas abaixo de 1s não são anunciadas e o aviso sai no máximo a cada 30s
(`MIN_REPORTED_WAIT` e `WAIT_REPORT_INTERVAL`).

O volume é grande: só a Fiat tem 585 modelos. A 40 requisições/min, uma carga completa de carros
leva dias — prefira `--brand` por marca, com `--resume`.

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

Os testes do worker fixam "hoje" em 06/2025, a tabela de referência mais nova das fixtures. Os
períodos de uma versão saem da data corrente, então com a data real todo período pedido cairia
fora do catálogo gravado e os testes passariam sem coletar nada.

## Web

- Busca full-text (`/?q=corsa`), com filtros de marca, combustível, ano e preço ao lado, ordenação
  por preço e cards embaixo.
- Página do modelo (`/modelo/?m=1-21-4712`): todas as versões daquele modelo, com preço.
- Detalhe da versão (`/veiculo/?v=code`): valor atual, variação 3/6/12 meses, gráfico do histórico.
- Comparador de até 4 versões (`/comparar/?v=code1,code2`), estado na querystring — link compartilhável.
- Rankings do mês (maiores altas e quedas): **ainda não feito.**

Templates de fragmento HTMX ficam em `web/templates/web/partials/` e devolvem só o pedaço trocado.

**O formulário de busca é um GET comum; o HTMX só troca `#results`.** Sem JS a mesma tela
funciona com recarga de página. `hx-push-url` mantém o link copiável, e os filtros voltam
iguais ao abrir a URL numa aba nova.

### A bandeja da comparação

Sem sessão e sem estado no cliente: o que já foi escolhido viaja na querystring de **toda** tela
(`web/selection.py`). É isso que mantém o link compartilhável e faz o "+ comparar" funcionar só
com recarga — mas cobra o preço de que toda tela que oferece o botão precisa repassar a bandeja
adiante, senão o clique seguinte parte do zero.

**Dois nomes de parâmetro, de propósito:**

- `v` — o que a página **é**: a versão no detalhe, a lista no comparador.
- `c` — o que a bandeja **carrega**, em todas as outras telas.

Eles colidiriam com um nome só: o detalhe já gasta `v` com a versão que mostra, então uma
bandeja com o mesmo nome sobrescreveria uma coisa ou outra. O comparador é onde a bandeja é
descontada, e por isso lê `v`.

Foi exatamente essa falta de repasse que quebrou o comparador antes: a view aceitava `?add=`,
mas **nenhum template gerava esse parâmetro** — todo botão apontava para `?v=<um código>`, o que
substituía a seleção em vez de somar, e a comparação nunca passava de uma versão.

Na página do modelo o botão **alterna sem sair da tela** (`selection.toggled` devolve `None`
quando a bandeja está cheia, e aí o template mostra um botão morto em vez de um link que
engoliria o clique). Escolher quatro versões do mesmo modelo seria quatro idas e voltas ao
comparador de outro jeito. A barra da bandeja (`partials/selection_bar.html`, incluída pelo
`base.html`) existe porque sem ela o clique só recarrega a página sem sinal nenhum de que algo
aconteceu.

### Códigos na URL

Três formatos, distinguidos pela contagem de partes — é isso que impede um resolver como o
outro:

- versão (5 partes): `tipo-marca-modelo-ano-combustível` → `1-21-4712-2017-5`
- modelo (3 partes): `tipo-marca-modelo` → `1-21-4712`
- marca (2 partes): `tipo-marca` → `1-21`

Montados com códigos da FIPE e não com PKs, para o link continuar valendo em outro banco. O tipo
de veículo vai na frente porque a FIPE numera marcas por tipo — a marca 21 de carro não é a
mesma de moto.

### Busca

`web/search.py` é o **único lugar com SQL cru** do projeto, e o único que sabe em qual banco
está rodando. Quem chama pede ids ranqueados e nunca vê `MATCH`, `bm25`, `tsquery` ou `ts_rank`.

**Dois dialetos, um significado.** `connection.vendor` escolhe o ramo; a migração
`web/migrations/0001` monta o índice equivalente em cada engine:

| | SQLite | Postgres |
|---|---|---|
| índice | tabela virtual FTS5 | tabela com coluna `tsvector` + GIN |
| prefixo | `"corsa"*` | `corsa:*` |
| E lógico | `AND` | `&` |
| acento | tokenizer `remove_diacritics 2` | configuração `simple_unaccent` |
| ranking | `bm25(...)`, menor é melhor | `ts_rank(..., 1)`, maior é melhor |
| peso nome/marca | 10 : 1 nos argumentos do `bm25` | rótulos `A`/`B` no vetor, pesos no `ts_rank` |
| desempate | `, rowid` | `, rowid` |

A configuração de texto é `simple`, não `portuguese`: nome de modelo não é prosa, e radicalizar
"Mille" ou "TETRAFUEL" como palavra portuguesa só distorceria.

**O acento sai na configuração, não na chamada.** `simple_unaccent` é `simple` mais o dicionário
`unaccent`, e o `to_tsvector` e o `to_tsquery` rodam os mesmos dicionários — então nomear a
configuração tira o acento dos dois lados de uma vez, que é exatamente o que o
`remove_diacritics 2` faz do lado do FTS5. Chamar `unaccent()` inline funcionaria, mas teria de
ser repetido em todo lugar, e `unaccent()` é STABLE: nunca poderia entrar numa expressão de
índice, nem numa coluna gerada.

**Coluna gerada não serve aqui**, por sinal: ela só enxerga a própria linha, e o índice precisa
do nome da marca, que está em outra tabela. É por isso que são triggers nos dois engines, e não
`GENERATED ALWAYS AS`.

O `, rowid` no fim das duas ordenações não é enfeite: com empate de ranking a ordem entre duas
consultas idênticas pode variar, e aí o paginador mostra um modelo duas vezes e outro nenhuma.

A normalização `1` do `ts_rank` (dividir por `1 + log(comprimento)`) existe para aproximar o
comportamento do bm25, que já corrige por comprimento sozinho — sem ela, nome comprido ganha de
nome curto só por ter mais palavras.

Armadilhas que já custaram tempo e não devem ser "simplificadas":

- **A tabela do índice é autônoma nos dois engines**, não `content='crawler_vehiclemodel'` nem
  uma coluna naquela tabela. `brand` é FK lá, não coluna: com external content o FTS5 emitiria
  `SELECT name, brand FROM crawler_vehiclemodel` e falharia, e de todo jeito o índice precisa do
  *nome* da marca, que está a um join de distância.
- **Existe trigger em `crawler_brand`.** O `sync.py` faz `update_or_create` na marca justamente
  para corrigir rename da FIPE; sem esse trigger o índice serviria o nome antigo. Triggers em
  vez de signals porque valem para qualquer escrita — um `bulk_create` futuro não dispara
  signal e dispara trigger.
- **Um comando por `execute()`.** O driver `sqlite3` roda só o primeiro de uma string com vários
  `CREATE`, em silêncio. Por isso a migração é uma lista de statements, e não um blob, mesmo no
  ramo do Postgres.
- **A migração usa `RunPython`, não `RunSQL`**, porque `RunSQL` não sabe ramificar por engine. O
  `schema_editor.execute(..., params=None)` é o que impede o driver de tentar interpretar `%`
  como placeholder.

**A defesa contra entrada hostil é o tokenizer, não o escape.** Um token é só letras e dígitos
(`[^\W_]+`), então `AND`, `-`, `*` e `((` do FTS5 e `&`, `|`, `!`, `:` e `<->` do `tsquery`
nunca chegam à consulta como sintaxe — em nenhum dos dois ramos. As aspas do FTS5 e o `:*` do
Postgres vêm por cima disso. `tokenize()` é compartilhada de propósito: os testes cobrem os dois
dialetos lado a lado, senão uma mudança nela quebraria o engine que não está rodando a suíte.

### Filtros e cards

O card é um `VehicleModel`, mas os filtros agem em `ModelYear`, então há uma etapa de agregação.

**Todo `values()` com `annotate()` ou `distinct()` nestes models precisa de `.order_by()`
vazio.** `PriceQuote` e `ModelYear` têm `Meta.ordering`, e o Django arrasta o campo de ordenação
para dentro da consulta — onde ele silenciosamente muda o resultado. Já mordeu duas vezes:

- `values().annotate()` em `PriceQuote`: o `-reference_table__year` entra no `GROUP BY` e parte
  cada modelo em uma linha por mês de referência;
- `values_list().distinct()` em `ModelYear`: o `-year` entra no `SELECT`, o `DISTINCT` passa a
  valer para o par, e o filtro de combustível exibia 126 checkboxes em vez de 7.

O sintoma é sempre "duplicou algo que deveria ser único", nunca um erro.

A faixa de preço usa só a referência mais recente: misturar meses daria um intervalo que não
existiu em mês nenhum. A contagem no card é de **versões** (ano *e* combustível), não de anos —
2017 flex e 2017 gasolina são duas versões num ano só. Por isso o card mostra também a **faixa de
ano/modelo** (`min_year`/`max_year`, mesma agregação): sem ela, "3 versões" não diz se são três
anos ou três combustíveis do mesmo ano. A faixa acompanha os filtros como o preço e a contagem —
descreve as versões que casaram, não o modelo inteiro.

O 0 km (ano 32000) vale como o ano mais novo: `>=` inclui, `<=` e `=` excluem. Isso é a
comparação numérica crua, sem código especial.

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

A página do modelo **ignora os filtros da busca**: a URL dela é compartilhável e não pode
mostrar coisas diferentes conforme o caminho que levou até ela.

Com um único mês coletado não há variação nem gráfico: as telas mostram `—` e uma nota, em vez
de zeros. `queries.variation` compara com o mês mais próximo **anterior ou igual** ao alvo (a
coleta é esparsa, o mês exato pode não existir) e devolve qual mês usou, para a tela dizer.

## Comandos

```bash
source venv/bin/activate.fish     # o shell do usuário é fish
docker compose up -d              # Postgres; o default das settings já aponta para ele
python manage.py runserver
python manage.py migrate
python manage.py test              # crawler + web; nenhum teste toca a rede
ruff check . && ruff format .
./tailwindcss -i web/static/web/src/input.css -o web/static/web/app.css --watch
```

### Banco

O `compose.yml` sobe só o Postgres — o Django continua rodando no venv do host. A porta é
publicada em `127.0.0.1`, então nada fica exposto na rede, e as credenciais default
(`carprice`/`carprice`) são de desenvolvimento.

**Rode a suíte nos dois engines antes de mexer na busca ou em migração.** É o único jeito de
saber que a compatibilidade continua real:

```bash
python manage.py test                                    # Postgres (o default)
DATABASE_URL=sqlite:///db.sqlite3 python manage.py test   # SQLite, sem container
```

Trocar o `DATABASE_URL` troca de banco, não migra os dados: cada engine tem o seu. Para levar o
banco de desenvolvimento de um para o outro é `dumpdata` no antigo e `loaddata` no novo, com o
`migrate` já rodado no destino.

### Setup do front (uma vez por máquina)

Nada disso é commitado — o binário está no `.gitignore` e os JS de terceiros são baixados, nunca
servidos por CDN. Sem esse passo as telas carregam sem estilo e a busca não filtra.

O `.gitignore` usa `/tailwindcss*` porque o asset do release mantém o sufixo de plataforma; se
você renomear o binário para `tailwindcss`, os comandos abaixo continuam valendo.

```bash
# Tailwind: binário standalone (v4, que é a sintaxe usada em input.css)
curl -sL -o tailwindcss https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-x64
chmod +x tailwindcss
./tailwindcss -i web/static/web/src/input.css -o web/static/web/app.css --minify

# JS de terceiros -> web/static/vendor/
curl -sL -o web/static/vendor/htmx.min.js       https://unpkg.com/htmx.org@2.0.10/dist/htmx.min.js
curl -sL -o web/static/vendor/apexcharts.min.js https://unpkg.com/apexcharts@5.16.0/dist/apexcharts.min.js
```

Alpine.js continua no stack, mas nenhuma tela precisa de estado de UI ainda — quando precisar,
baixe `alpinejs@3/dist/cdn.min.js` como `vendor/alpine.min.js` e carregue **depois** do HTMX.

## Testes

Fixtures são respostas reais da FIPE gravadas em `crawler/tests/fixtures/` e servidas por
`FakeFipeClient` (`crawler/tests/fake_client.py`), que também registra as chamadas para os testes
afirmarem sobre o tráfego. Ao adicionar um endpoint, grave a fixture correspondente.

Os testes da `web` (pacote `web/tests/`, com os construtores em `factories.py`) montam os dados
na mão e cobrem o que quebra em silêncio:
round-trip dos dois códigos, o fallback de mês da variação, a sintaxe dos dois dialetos de busca
(inclusive entrada hostil), os triggers do índice exercitados pelo ORM, o limite de 4 no
comparador e o repasse da bandeja entre as telas.

Os testes de busca que tocam o banco (`SearchTests`, `TriggerTests`) são **o teste de
compatibilidade entre engines**: são os mesmos asserts nos dois, e é rodá-los em Postgres e em
SQLite que prova que os dois ramos de `search.py` querem dizer a mesma coisa.

## Estado atual

O crawler está implementado e verificado contra a API real. A `web` tem busca full-text, página
do modelo, detalhe e comparador funcionando; falta o ranking de altas e quedas.

**Os dados de desenvolvimento ainda estão no `db.sqlite3`.** O Postgres do compose nasce vazio;
até rodar `dumpdata`/`loaddata`, as telas apontando para ele não mostram veículo nenhum.

Esse banco SQLite tem **um único mês** (08/2026) e 5 marcas com preços (Fiat, GM, BYD, Citroën,
GEELY) — por isso as telas foram feitas para o caso sem histórico. Para ver gráfico e variação
de verdade, colete um segundo mês: `crawl_fipe --reference 2026-07 --brand 21`.

A coleta da GM parou no "AGILE", então **"corsa" não devolve nada ainda** — não é falha da
busca. Para testar com caso real use "siena", "uno" ou "aircross".

Os combustíveis 6 (Híbrido) e 7 (Tetrafuel) já foram nomeados; o próximo código desconhecido
volta a aparecer como número cru, que é o comportamento desejado.

`TIME_ZONE` é `America/Sao_Paulo` e `LANGUAGE_CODE` já virou `pt-br`. Pendências conhecidas:
`SECRET_KEY` está no `settings.py`, `ALLOWED_HOSTS` está vazio, e `ruff` não está instalado no
venv (o comando do check não roda).
