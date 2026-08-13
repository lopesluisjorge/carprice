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
- SQLite hoje; `DATABASE_URL` via `django-environ` para migrar a Postgres sem refatorar
- Front: templates Django + HTMX (fragmentos) + Alpine.js (estado leve de UI)
- Tailwind pelo **binário standalone** — sem npm, sem `node_modules`
- Gráficos: ApexCharts
- Testes: pytest + pytest-django + `responses` (mock de HTTP)

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
```

Fronteira que não deve ser cruzada: **`web` nunca chama a FIPE**, só consulta o banco.
`crawler/fipe/` nunca importa models — recebe e devolve dataclasses.

## Modelo de dados

```
ReferenceTable   fipe_code, month, year              # tabela de referência mensal da FIPE
Brand            fipe_code, name, vehicle_type       # CAR=1, MOTORCYCLE=2, TRUCK=3
VehicleModel     brand FK, fipe_code, name
ModelYear        vehicle_model FK, year, fuel_type, fipe_year_code
PriceQuote       model_year FK, reference_table FK, value, fipe_code, collected_at
                 unique(model_year, reference_table)
CrawlRun         reference_table FK, status, started_at, finished_at, counters, last_error
CrawlCheckpoint  crawl_run FK, brand FK, done        # suporte a --resume
```

`PriceQuote` é a tabela grande (uma linha por versão/ano/mês) — mantenha o índice em
`(model_year, reference_table)` e evite queries que a varram sem filtro de `reference_table`.

`vehicle_type` existe desde o início, mas **só carros são coletados por enquanto**. Motos e
caminhões devem funcionar sem migração: qualquer código novo trata o tipo como parâmetro, nunca
como constante embutida.

## Coleta

Endpoints usados (`https://veiculos.fipe.org.br/api/veiculos/`): `ConsultarTabelaDeReferencia`,
`ConsultarMarcas`, `ConsultarModelos`, `ConsultarAnoModelo`, `ConsultarValorComTodosParametros`.

```bash
python manage.py crawl_fipe                      # mês corrente, só carros
python manage.py crawl_fipe --reference 2024-01  # backfill de um mês específico
python manage.py crawl_fipe --brand 21 --limit 50
python manage.py crawl_fipe --resume             # retoma o último CrawlRun incompleto
python manage.py crawl_fipe --dry-run
```

Invariantes:

- **Idempotente.** Rodar duas vezes o mesmo mês não duplica nem altera nada.
- **Retomável.** Falha no meio deixa `CrawlRun` + `CrawlCheckpoint` consistentes para `--resume`.
- **Educado.** Delay configurável entre requests, sem paralelismo agressivo, `User-Agent` honesto.
  Não aumente a taxa de requisições para "acelerar" sem o usuário pedir.

## Web

- Busca em cascata marca → modelo → ano, com HTMX trocando fragmentos de `<select>`.
- Detalhe do veículo: valor atual, variação 3/6/12 meses, gráfico de linha do histórico.
- Comparador de até 4 versões, com estado na querystring (`?v=code1,code2`) para link compartilhável.
- Rankings do mês: maiores altas e quedas.

Templates de fragmento HTMX ficam em `web/templates/web/partials/` e devolvem só o pedaço trocado.

## Comandos

```bash
source venv/bin/activate.fish     # o shell do usuário é fish
python manage.py runserver
python manage.py migrate
pytest                            # nenhum teste toca a rede
ruff check . && ruff format .
./tailwindcss -i web/static/web/src/input.css -o web/static/web/app.css --watch
```

## Testes

Fixtures são respostas reais da FIPE gravadas em `crawler/tests/fixtures/` e servidas por
`responses`. Ao adicionar um endpoint, grave a fixture correspondente. Cobertura esperada:
parsers, idempotência do upsert, retomada após falha e as views de comparação.

## Estado atual

Projeto recém-iniciado: apps `crawler` e `web` existem mas estão vazias e ainda não estão em
`INSTALLED_APPS`. Pendências conhecidas: falta `.gitignore` (há `__pycache__` commitado),
`SECRET_KEY` está no `settings.py` e `LANGUAGE_CODE`/`TIME_ZONE` ainda são `en-us`/`UTC` —
devem virar `pt-br`/`America/Sao_Paulo`.
