# Deploy

Alvo: uma VPS Linux, sem Docker para a aplicação, com tudo sob systemd. O `compose.yml` da
raiz continua sendo coisa de desenvolvimento — aqui o Postgres é o do sistema.

## O que roda no servidor

Quatro processos, e a distinção entre os dois do meio é a parte que não dá para improvisar:

| Processo | Unidade | Papel |
|---|---|---|
| nginx | `nginx.service` | TLS, arquivos estáticos, proxy reverso |
| gunicorn | `carprice-web.service` | a aplicação Django; **só lê o banco** |
| worker da fila | `carprice-worker.service` | **único** processo que fala com a FIPE |
| Postgres | `postgresql.service` | o banco |

**Sem o `carprice-worker` rodando, nada é coletado.** A busca só grava o pedido; quem vai à FIPE
é o worker. E ele precisa ser **um só na infraestrutura inteira**: a cota de 40 requisições/min
vive na janela deslizante em memória de um `FipeClient`, então dois workers dão 80 req/min e
trazem os 429 de volta. O `flock` em `CRAWL_QUEUE_LOCK_PATH` protege contra um segundo processo
na mesma máquina — **não** contra uma segunda máquina.

## Pré-requisitos

- Debian 13 / Ubuntu 24.04 ou equivalente, com Python 3.14 disponível
- Postgres 13 ou mais novo (a extensão `unaccent` é *trusted* a partir do 13, e é isso que
  permite a migração criá-la sem superusuário)
- Um domínio apontando para o IP da VPS, para o certificado

## 1. Pacotes do sistema

```bash
sudo apt update
sudo apt install -y python3.14 python3.14-venv git nginx postgresql certbot python3-certbot-nginx
```

## 2. Usuário e diretórios

```bash
sudo useradd --system --home-dir /srv/carprice --shell /usr/sbin/nologin carprice
sudo mkdir -p /srv/carprice /etc/carprice
sudo chown carprice:carprice /srv/carprice
```

`/var/lib/carprice` e `/run/carprice` não precisam ser criados à mão: as unidades declaram
`StateDirectory` e `RuntimeDirectory`, e o systemd os cria com o dono certo.

## 3. Banco

```bash
sudo -u postgres createuser --pwprompt carprice
sudo -u postgres createdb --owner=carprice --encoding=UTF8 --locale=C.UTF-8 --template=template0 carprice
```

O dono do banco basta — a migração `web/0001` roda `CREATE EXTENSION unaccent` e
`CREATE TEXT SEARCH CONFIGURATION`, e as duas coisas o dono pode fazer. Se o Postgres for
gerenciado e proibir a extensão, instale-a à mão antes de migrar:

```bash
sudo -u postgres psql -d carprice -c 'CREATE EXTENSION IF NOT EXISTS unaccent'
```

## 4. Código e venv

```bash
sudo -u carprice git clone <repo> /srv/carprice
cd /srv/carprice
sudo -u carprice python3.14 -m venv venv
sudo -u carprice venv/bin/pip install -r requirements.txt
```

O binário do Tailwind **não** é instalado aqui: `web/static/web/app.css` vem pronto do
repositório. O `web/tailwind/input.css` é entrada de build e mora fora do `static/` justamente
para o `collectstatic` não tentar copiá-lo.

## 5. Configuração

```bash
sudo -u carprice venv/bin/python -c \
  "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Ponha o resultado em `/etc/carprice/env` — o arquivo que as duas unidades leem. Os nomes são os
mesmos do `.env.example`, sem aspas e sem `export`:

```ini
DEBUG=False
SECRET_KEY=<o valor gerado acima>
ALLOWED_HOSTS=carprice.com.br,www.carprice.com.br
DATABASE_URL=postgres://carprice:<senha>@127.0.0.1:5432/carprice
STATIC_ROOT=/srv/carprice/staticfiles
CRAWL_QUEUE_LOCK_PATH=/var/lib/carprice/crawl_queue.lock
```

```bash
sudo chown root:carprice /etc/carprice/env
sudo chmod 640 /etc/carprice/env
```

`DEBUG` não tem default permissivo: sem `SECRET_KEY` ou sem `ALLOWED_HOSTS` a aplicação **se
recusa a subir**, com a mensagem dizendo qual falta. É proposital — um servidor mal configurado
para de pé em vez de servir traceback para a internet.

`CSRF_TRUSTED_ORIGINS` sai de `ALLOWED_HOSTS` como `https://<host>`; só declare se o esquema ou a
porta forem outros.

## 6. Migrar e montar os estáticos

```bash
cd /srv/carprice
sudo -u carprice --preserve-env venv/bin/python manage.py check --deploy
sudo -u carprice venv/bin/python manage.py migrate
sudo -u carprice venv/bin/python manage.py collectstatic --noinput
sudo -u carprice venv/bin/python manage.py createsuperuser
```

Os comandos manuais não leem `/etc/carprice/env` sozinhos. Carregue antes:

```bash
set -a; . /etc/carprice/env; set +a
```

O `check --deploy` deve sair limpo **exceto** por `security.W004` (HSTS), que é esperado neste
ponto — veja o passo 8.

## 7. Unidades e nginx

```bash
sudo cp deploy/carprice-web.service deploy/carprice-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now carprice-web carprice-worker

sudo cp deploy/nginx.conf /etc/nginx/sites-available/carprice
sudo ln -s /etc/nginx/sites-available/carprice /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Ajuste `server_name` no arquivo do nginx antes de recarregar. Depois, o certificado:

```bash
sudo certbot --nginx -d carprice.com.br -d www.carprice.com.br
```

O certbot reescreve o arquivo do nginx no lugar, acrescentando o bloco 443 e o redirect da 80.
Rode-o **depois** que o HTTP simples já responde, senão a validação falha.

Firewall, se houver:

```bash
sudo ufw allow 'Nginx Full' && sudo ufw allow OpenSSH && sudo ufw enable
```

## 8. HSTS, só depois que o HTTPS estiver de pé

`SECURE_HSTS_SECONDS` nasce em `0` de propósito: o navegador **memoriza** a diretiva, então um
valor de um ano posto cedo demais tranca os visitantes fora do domínio se o certificado quebrar
depois. Confirme que o HTTPS responde, e só então suba em degraus, reiniciando a cada um:

```ini
SECURE_HSTS_SECONDS=3600          # um dia depois: 86400
# depois de uma semana tranquila:
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=True
SECURE_HSTS_PRELOAD=True
```

Com o último degrau o `check --deploy` sai sem nenhum aviso.

## 9. Dados

O Postgres novo nasce vazio, e o banco de desenvolvimento é SQLite. Para levar o que já foi
coletado:

```bash
# na máquina de desenvolvimento
DATABASE_URL=sqlite:///db.sqlite3 python manage.py dumpdata crawler \
  --natural-foreign --natural-primary --indent 2 > carprice.json

# no servidor, com o migrate já rodado
sudo -u carprice venv/bin/python manage.py loaddata carprice.json
```

Os triggers do índice de busca disparam no `loaddata`, então a busca já vem indexada. Sem isso,
o servidor sobe funcionando e vazio: as primeiras buscas agendam a coleta e o worker preenche.

## Atualizar

```bash
sudo -u carprice /srv/carprice/deploy/update.sh
```

Dependências, migrações, estáticos e só então o reload — nessa ordem, para que os workers que
sobem nunca sejam mais novos que o schema nem mais velhos que o manifesto que vão servir. O
`reload` do gunicorn é gracioso: as requisições em voo terminam.

O script chama `sudo systemctl`, então o usuário `carprice` precisa disso liberado:

```ini
# /etc/sudoers.d/carprice
carprice ALL=(root) NOPASSWD: /bin/systemctl reload carprice-web, /bin/systemctl restart carprice-worker
```

## Operação

```bash
systemctl status carprice-web carprice-worker
journalctl -u carprice-web -f
journalctl -u carprice-worker -f      # é aqui que aparece o progresso da coleta
```

Coleta em massa continua sendo manual, e é longa — uma carga completa de carros leva dias a 40
req/min. Rode fora do worker, e **pare o worker antes**, senão os dois somam na cota:

```bash
sudo systemctl stop carprice-worker
set -a; . /etc/carprice/env; set +a
sudo -u carprice --preserve-env venv/bin/python manage.py crawl_fipe --brand 21 --resume
sudo systemctl start carprice-worker
```

## Backup

O que não se recupera é o histórico de preços: a FIPE só publica o mês vigente, então um mês
perdido é perdido para sempre.

```ini
# /etc/systemd/system/carprice-backup.service  (+ .timer diário)
ExecStart=/usr/bin/pg_dump -Fc -f /var/backups/carprice-%%Y%%m%%d.dump carprice
```

## Checklist

- [ ] `check --deploy` limpo (só `W004` até o HSTS subir)
- [ ] `systemctl is-enabled carprice-web carprice-worker` → `enabled` nos dois
- [ ] HTTPS respondendo e HTTP redirecionando
- [ ] Uma busca no site retorna, e o `journalctl -u carprice-worker` mostra a coleta andando
- [ ] `/admin/` entra com o superusuário
- [ ] Um `pg_dump` já rodou
- [ ] **Só uma máquina** rodando `carprice-worker`
