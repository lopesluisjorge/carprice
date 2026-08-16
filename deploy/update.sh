#!/usr/bin/env bash
# Deploy a new revision onto a server already set up by docs/deploy.md.
# Run as the user that owns /srv/carprice:
#     sudo -u carprice ./deploy/update.sh
#
# Order is not arbitrary: dependencies before migrations (a migration may need a
# new library), migrations before collectstatic, and both before the reload, so
# the workers that come up are never newer than the schema or older than the
# manifest they will be asked to serve.
set -euo pipefail

APP_DIR=${APP_DIR:-/srv/carprice}
VENV=${VENV:-$APP_DIR/venv}
PYTHON=$VENV/bin/python

cd "$APP_DIR"

echo '==> Código'
sudo -u carprice git pull --ff-only

echo '==> Dependências'
# pip-sync, not pip install: it also removes what left requirements.txt, so the
# venv matches the file instead of accumulating.
if [ -x "$VENV/bin/pip-sync" ]; then
    "$VENV/bin/pip-sync" requirements.txt
else
    "$VENV/bin/pip" install --upgrade -r requirements.txt
fi

echo '==> Configuração'
# Errors abort the deploy here, before anything is restarted. Warnings only
# print — SECURE_HSTS_SECONDS=0 is one, and is expected until HTTPS has been
# confirmed working (docs/deploy.md).
sudo -u carprice "$PYTHON" manage.py check --deploy

echo '==> Migrações'
sudo -u carprice "$PYTHON" manage.py migrate --noinput

echo '==> Estáticos'
# app.css comes from git already built — the tailwind binary is a development
# tool and is not installed here.
sudo -u carprice "$PYTHON" manage.py collectstatic --noinput --clear

echo '==> Bytecode'
# The units run with ProtectSystem=strict, so the tree is read-only at runtime
# and Python cannot cache .pyc itself. Compiling now keeps the first request
# after a deploy from paying for every import.
#
# -x is matched against the whole path, and it skips the test packages: neither
# the web process nor the worker ever imports them, so compiling them only
# writes bytecode nobody loads. It also keeps the step off the one directory
# tree that has no business existing on a server at all.
sudo -u carprice "$PYTHON" -m compileall -q -x '(^|/)tests?(/|$)' \
    "$APP_DIR/carprice" "$APP_DIR/crawler" "$APP_DIR/web" || true

echo '==> Serviços'
# reload = graceful for gunicorn (in-flight requests finish). The worker has no
# reload, and restarting it is cheap: an interrupted pass is picked up again by
# reclaim_stale_requests.
sudo systemctl reload carprice-web
sudo systemctl restart carprice-worker

echo '==> Pronto'
systemctl --no-pager --lines=0 status carprice-web carprice-worker
