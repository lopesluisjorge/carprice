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
- **Zero dependências além do Django.** O cliente HTTP usa `urllib` da stdlib e os testes usam o
  runner do Django. Se `requests` for instalado, a troca se resume a `FipeClient._post`.
- SQLite hoje; `DATABASE_URL` via `django-environ` para migrar a Postgres sem refatorar
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
nem checkpoints, e recusa `--brands-only`, `--limit` e `--resume`. Ao contrário do resto da
varredura — que usa `get_or_create` e só preenche lacunas — ele usa `update_or_create` em modelo
e ano/modelo: o objetivo é manter o catálogo fresco, então uma renomeação da FIPE é corrigida
aqui e reindexada no FTS pelos triggers. É o passo para popular a busca de uma marca sem gastar a
cota de cotações.

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

- Busca full-text (`/?q=corsa`), com filtros de combustível e ano ao lado e cards embaixo.
- Página do modelo (`/modelo/?m=1-21-4712`): todas as versões daquele modelo, com preço.
- Detalhe da versão (`/veiculo/?v=code`): valor atual, variação 3/6/12 meses, gráfico do histórico.
- Comparador de até 4 versões (`/comparar/?v=code1,code2`), estado na querystring — link compartilhável.
- Rankings do mês (maiores altas e quedas): **ainda não feito.**

Templates de fragmento HTMX ficam em `web/templates/web/partials/` e devolvem só o pedaço trocado.

**O formulário de busca é um GET comum; o HTMX só troca `#results`.** Sem JS a mesma tela
funciona com recarga de página. `hx-push-url` mantém o link copiável, e os filtros voltam
iguais ao abrir a URL numa aba nova.

### Códigos na URL

Dois formatos, distinguidos pela contagem de partes — é isso que impede um resolver como o
outro:

- versão (5 partes): `tipo-marca-modelo-ano-combustível` → `1-21-4712-2017-5`
- modelo (3 partes): `tipo-marca-modelo` → `1-21-4712`

Montados com códigos da FIPE e não com PKs, para o link continuar valendo em outro banco. O tipo
de veículo vai na frente porque a FIPE numera marcas por tipo — a marca 21 de carro não é a
mesma de moto.

### Busca

`web/search.py` é o **único lugar com SQL cru** do projeto. Quem chama pede ids ranqueados e
nunca vê `MATCH` nem `bm25`; migrar para Postgres é reescrever esse módulo e uma migração, não
as telas.

Três armadilhas que já custaram tempo e não devem ser "simplificadas":

- **A tabela FTS5 é autônoma, não `content='crawler_vehiclemodel'`.** `brand` é FK lá, não
  coluna: com external content o FTS5 emitiria `SELECT name, brand FROM crawler_vehiclemodel` e
  falharia.
- **Existe trigger em `crawler_brand`.** O `sync.py` faz `update_or_create` na marca justamente
  para corrigir rename da FIPE; sem esse trigger o índice serviria o nome antigo. Triggers em
  vez de signals porque valem para qualquer escrita — um `bulk_create` futuro não dispara
  signal e dispara trigger.
- **`RunSQL` recebe uma lista.** O driver `sqlite3` roda um comando por `execute()`; uma string
  com vários `CREATE` aplicaria só o primeiro, em silêncio.

O termo do usuário vira literal entre aspas com prefixo (`corsa` → `"corsa"*`), o que neutraliza
`AND`, `-`, `*` e `((` digitados. Acento não precisa de tratamento: o tokenizer
`remove_diacritics 2` processa índice e consulta, então "citroen" acha "Citroën".

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
2017 flex e 2017 gasolina são duas versões num ano só.

O 0 km (ano 32000) vale como o ano mais novo: `>=` inclui, `<=` e `=` excluem. Isso é a
comparação numérica crua, sem código especial.

A página do modelo **ignora os filtros da busca**: a URL dela é compartilhável e não pode
mostrar coisas diferentes conforme o caminho que levou até ela.

Com um único mês coletado não há variação nem gráfico: as telas mostram `—` e uma nota, em vez
de zeros. `queries.variation` compara com o mês mais próximo **anterior ou igual** ao alvo (a
coleta é esparsa, o mês exato pode não existir) e devolve qual mês usou, para a tela dizer.

## Comandos

```bash
source venv/bin/activate.fish     # o shell do usuário é fish
python manage.py runserver
python manage.py migrate
python manage.py test              # crawler + web; nenhum teste toca a rede
ruff check . && ruff format .
./tailwindcss -i web/static/web/src/input.css -o web/static/web/app.css --watch
```

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
round-trip dos dois códigos, o fallback de mês da variação, a sintaxe do FTS5 (inclusive
entrada hostil), os triggers do índice exercitados pelo ORM e o limite de 4 no comparador.

## Estado atual

O crawler está implementado e verificado contra a API real. A `web` tem busca full-text, página
do modelo, detalhe e comparador funcionando; falta o ranking de altas e quedas.

O banco de desenvolvimento tem **um único mês** (08/2026) e 5 marcas com preços (Fiat, GM,
BYD, Citroën, GEELY) — por isso as telas foram feitas para o caso sem histórico. Para ver
gráfico e variação de verdade, colete um segundo mês: `crawl_fipe --reference 2026-07 --brand 21`.

A coleta da GM parou no "AGILE", então **"corsa" não devolve nada ainda** — não é falha da
busca. Para testar com caso real use "siena", "uno" ou "aircross".

Os combustíveis 6 (Híbrido) e 7 (Tetrafuel) já foram nomeados; o próximo código desconhecido
volta a aparecer como número cru, que é o comportamento desejado.

`TIME_ZONE` é `America/Sao_Paulo` e `LANGUAGE_CODE` já virou `pt-br`. Pendências conhecidas:
`SECRET_KEY` está no `settings.py`, `ALLOWED_HOSTS` está vazio, e `ruff` não está instalado no
venv (o comando do check não roda).
